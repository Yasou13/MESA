"""Extract document text and character offsets from supported uploads."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import PurePath
from typing import BinaryIO
from xml.etree import ElementTree
from zipfile import ZipFile

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from openpyxl import load_workbook
from pypdf import PdfReader

from mesa_storage.document_locations import DocumentLocations

MAX_FILE_BYTES = 50_000_000
MAX_DOCUMENT_BYTES = 5_000_000
_FILENAME_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class DocumentFileError(ValueError):
    """Malformed, unsupported, encrypted, or empty document."""


class DocumentTextTooLargeError(DocumentFileError):
    """The extracted text exceeded the document admission limit."""


def _pdf(file: BinaryIO) -> Iterator[tuple[str, int, str | None]]:
    reader = PdfReader(file)
    if reader.is_encrypted:
        raise DocumentFileError("Encrypted PDFs are not supported.")
    for number, page in enumerate(reader.pages, start=1):
        text = page.extract_text().strip()
        if text:
            yield text, number, None


def _word_blocks(blocks: Iterator[Paragraph | Table]) -> Iterator[str]:
    for block in blocks:
        if isinstance(block, Paragraph):
            yield block.text
        else:
            for row in block.rows:
                yield "\t".join(
                    "\n".join(_word_blocks(cell.iter_inner_content()))
                    for cell in row.cells
                )


def _xlsx(file: BinaryIO) -> Iterator[tuple[str, int, str | None]]:
    workbook = load_workbook(file, read_only=True, data_only=True, keep_links=False)
    try:
        for sheet in workbook.worksheets:
            # Exporters can leave incorrect dimensions in the worksheet.
            sheet.reset_dimensions()
            headers: tuple[object, ...] | None = None
            for number, values in enumerate(sheet.iter_rows(values_only=True), start=1):
                if not any(value is not None for value in values):
                    continue
                if headers is None:
                    headers = values
                    continue
                pairs = (
                    f"{headers[i] if i < len(headers) and headers[i] is not None else f'Column {i + 1}'}: {value}"
                    for i, value in enumerate(values)
                    if value is not None
                )
                yield "\n".join(pairs), number, sheet.title
    finally:
        workbook.close()


class _UdfTreeBuilder(ElementTree.TreeBuilder):
    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise DocumentFileError("Document type declarations are not supported in UDF.")


def _udf(file: BinaryIO) -> str:
    with ZipFile(file) as archive:
        entry = archive.getinfo("content.xml")
        if entry.file_size > MAX_FILE_BYTES:
            raise DocumentFileError("The UDF content.xml entry is too large.")
        with archive.open(entry) as content:
            parser = ElementTree.XMLParser(target=_UdfTreeBuilder())
            root = ElementTree.parse(content, parser=parser).getroot()
    if root.tag != "template":
        raise DocumentFileError("The UDF content.xml does not contain a template.")
    return root.findtext("content", "")


def _extract_segments(
    segments: Iterator[tuple[str, int, str | None]],
) -> tuple[str, DocumentLocations]:
    parts: list[str] = []
    locations: DocumentLocations = []
    bytes_used = 0
    end = 0
    for text, number, sheet in segments:
        if parts:
            previous_end, previous_number, previous_sheet = locations[-1]
            separator = "\n\n" if sheet is None or sheet != previous_sheet else "\n"
            parts.append(separator)
            locations[-1] = (
                previous_end + len(separator),
                previous_number,
                previous_sheet,
            )
            end += len(separator)
            bytes_used += len(separator)
        bytes_used += len(text.encode("utf-8"))
        if bytes_used > MAX_DOCUMENT_BYTES:
            raise DocumentTextTooLargeError()
        parts.append(text)
        end += len(text)
        locations.append((end, number, sheet))
    return "".join(parts), locations


def _sanitize_upload_filename(filename: str) -> str:
    """Validate and extract only the safe basename of an uploaded filename."""
    if not filename or _FILENAME_CONTROL_RE.search(filename):
        raise DocumentFileError("Invalid file name.")
    # Normalize both POSIX and Windows separators; never trust directory segments.
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not basename or basename in {".", ".."}:
        raise DocumentFileError("Invalid file name.")
    return basename


def extract_document_file(
    file: BinaryIO, filename: str
) -> tuple[str, DocumentLocations]:
    """Extract text without changing the original file or retaining its bytes."""
    safe_name = _sanitize_upload_filename(filename)
    extension = PurePath(safe_name).suffix.lower()
    if extension not in {".pdf", ".docx", ".udf", ".xlsx", ".txt", ".md"}:
        raise DocumentFileError(
            "Supported files are PDF, DOCX, UDF, XLSX, TXT, and MD."
        )
    try:
        if extension in {".docx", ".xlsx", ".udf"}:
            signature = file.read(8)
            file.seek(0)
            # Password-protected OOXML packages are wrapped in OLE containers.
            if signature == bytes.fromhex("D0 CF 11 E0 A1 B1 1A E1"):
                raise DocumentFileError("Encrypted Office files are not supported.")
            with ZipFile(file) as archive:
                entries = archive.infolist()
                if any(entry.flag_bits & 1 for entry in entries):
                    raise DocumentFileError("Encrypted files are not supported.")
                # Parsers load decompressed XML into memory; bound it before parsing.
                if sum(entry.file_size for entry in entries) > MAX_FILE_BYTES:
                    raise DocumentFileError("The file expands to more than 50 MB.")
        if extension == ".pdf":
            text, locations = _extract_segments(_pdf(file))
            if not text:
                raise DocumentFileError("The PDF has no extractable text layer.")
            return text, locations
        if extension == ".xlsx":
            text, locations = _extract_segments(_xlsx(file))
            if not text:
                raise DocumentFileError("The spreadsheet contains no data rows.")
            return text, locations
        if extension == ".docx":
            parts: list[str] = []
            size = 0
            for part in _word_blocks(Document(file).iter_inner_content()):
                size += len(part.encode("utf-8")) + bool(parts)
                if size > MAX_DOCUMENT_BYTES:
                    raise DocumentTextTooLargeError()
                parts.append(part)
            text = "\n".join(parts)
        elif extension == ".udf":
            text = _udf(file)
        else:
            raw = file.read(MAX_DOCUMENT_BYTES + 4).removeprefix(b"\xef\xbb\xbf")
            if len(raw) > MAX_DOCUMENT_BYTES:
                raise DocumentTextTooLargeError()
            text = raw.decode("utf-8")
    except DocumentFileError:
        raise
    except UnicodeDecodeError as exc:
        raise DocumentFileError("The file contains invalid text encoding.") from exc
    except Exception as exc:
        raise DocumentFileError(
            f"The {extension[1:].upper()} file could not be read."
        ) from exc
    if not text.strip():
        raise DocumentFileError("The file contains no text.")
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise DocumentTextTooLargeError()
    return text, []
