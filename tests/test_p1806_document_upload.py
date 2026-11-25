"""File ingestion, exact citation offsets and SDK transport (D95, D99)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from test_p1803_citation_span_and_text import _drain_work
from test_p1803_citation_span_and_text import span_runtime as span_runtime

from mesa_client import AsyncMesaV4Client, MesaV4Client

FIXTURES = Path(__file__).parent / "fixtures" / "document_upload"
SCOPE = {"tenant_id": "t1", "workspace_id": "ws1", "dataset_id": "ds1"}


def fields(document_id: str) -> dict[str, str]:
    return {
        **SCOPE,
        "document_id": document_id,
        "title": "Uploaded document",
        "source_ref": f"file://{document_id}",
    }


async def upload(client, name, *, document_id="upload", extra=None, content=None):
    return await client.post(
        "/v4/documents/upload",
        data={**fields(document_id), **(extra or {})},
        files={
            "file": (
                name,
                content if content is not None else (FIXTURES / name).read_bytes(),
            )
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "expected", "query", "location"),
    [
        (
            "sample.pdf",
            "The first page records the migration.\n\nThe third page provides the heliotrope warranty.",
            "heliotrope",
            {"page": 3},
        ),
        (
            "sample.docx",
            "İlk paragraf\ntable-first\ttable-second\nSon paragraf",
            "paragraf",
            {},
        ),
        ("sample.udf", "Örnek UYAP belgesi.\n", "UYAP", {}),
        ("sample.txt", "MESA düz metin.\n", "MESA", {}),
        ("sample.md", "# MESA\n\nÇalışma notu.\n", "Çalışma", {}),
        (
            "sample.xlsx",
            "Malzeme: bakır\nAdet: 0\nMalzeme: kuvars\nAdet: 12\n\nDosya: zirkon\nDurum: False",
            "kuvars",
            {"sheet": "Sipariş", "row_start": 2, "row_end": 4},
        ),
    ],
    ids=["pdf", "docx", "udf", "txt", "markdown", "xlsx"],
)
async def test_upload_and_search(span_runtime, filename, expected, query, location):
    async with span_runtime() as env:
        owner, reader = env["owner"], env["reader"]
        response = await upload(owner, filename)
        assert response.status_code == 202, response.text
        revision_id = response.json()["revision_id"]

        await _drain_work(env["dao"])

        status = await reader.get("/v4/documents/upload", params=SCOPE)
        assert status.json()["revisions"][0]["stage"] == "active"
        result_text = await reader.get("/v4/documents/upload/text", params=SCOPE)
        assert result_text.json()["text"] == expected

        search = await reader.post(
            "/v4/memory/search",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_ids": ["ds1"],
                "document_ids": ["upload"],
                "query": query,
            },
        )
        assert search.status_code == 200, search.text
        citation = search.json()["results"][0]
        assert citation["revision_id"] == revision_id
        location_keys = {"page", "sheet", "row_start", "row_end"}
        assert {
            key: citation[key] for key in location_keys & citation.keys()
        } == location
        assert {key: citation["provenance"][0][key] for key in location} == location
        span = citation["span"]
        passage = citation["provenance"][0]["literal_value"] or citation["evidence_span"]
        assert expected[span["start"] : span["end"]] == passage
        cited_text = await reader.get("/v4/documents/upload/text", params={**SCOPE, **span})
        assert cited_text.json()["text"] == passage

        retry = await upload(owner, filename)
        assert retry.status_code == 202
        assert retry.json()["revision_id"] == revision_id
        assert retry.json()["duplicate"] is True


@pytest.mark.asyncio
async def test_upload_dated_history_and_scope(span_runtime):
    async with span_runtime() as env:
        owner = env["owner"]
        for role in ("reader", "outsider"):
            denied = await upload(env[role], "sample.pdf")
            assert denied.status_code == 403

        newer = await upload(
            owner,
            "sample.txt",
            extra={"valid_from": "2025-01-01T00:00:00Z"},
        )
        assert newer.status_code == 202, newer.text
        await _drain_work(env["dao"])
        older = await upload(
            owner,
            "sample.pdf",
            extra={"valid_from": "2024-01-01T03:00:00+03:00"},
        )
        assert older.status_code == 202, older.text
        await _drain_work(env["dao"])

        search = await env["reader"].post(
            "/v4/memory/search",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_ids": ["ds1"],
                "query": "heliotrope",
                "valid_at": "2024-06-01T00:00:00Z",
            },
        )
        item = search.json()["results"][0]
        assert item["revision_id"] == older.json()["revision_id"]
        assert item["valid_from"] == "2024-01-01T00:00:00Z"
        assert item["valid_to"] == "2025-01-01T00:00:00Z"
        assert item["page"] == 3

        active = await owner.get("/v4/documents/upload/text", params=SCOPE)
        assert active.json()["revision_id"] == newer.json()["revision_id"]


@pytest.mark.asyncio
async def test_upload_rejects_invalid_files_without_creating_document(span_runtime):
    async with span_runtime() as env:
        owner = env["owner"]
        for name, payload, message in [
            ("scan.pdf", None, "no extractable text layer"),
            ("encrypted.pdf", None, "Encrypted PDFs"),
            ("archive.exe", b"arbitrary", "Supported files"),
            ("broken.docx", b"not a document", "DOCX file could not be read"),
            ("invalid.txt", b"\xff", "invalid text encoding"),
        ]:
            response = await upload(owner, name, content=payload)
            assert response.status_code == 422, response.text
            assert message in response.json()["detail"]

        response = await upload(owner, "large.txt", content="é".encode() * 2_500_001)
        assert response.status_code == 413
        assert "5,000,000 UTF-8 bytes" in response.json()["detail"]

        with patch("mesa_api.v4_router.MAX_FILE_BYTES", 64):
            response = await upload(owner, "large.txt", content=b"x" * 65)
            assert response.status_code == 413
            response = await upload(
                owner, "oversize.txt", content=b"x" * (1024 * 1024 + 65)
            )
            assert response.status_code == 413

        response = await upload(owner, "sample.txt", extra={"valid_from": "2025-01-01"})
        assert response.status_code == 422
        response = await owner.get("/v4/documents/upload", params=SCOPE)
        assert response.status_code == 404


def test_sync_sdk_add_file_sends_multipart():
    def receive(request):
        assert str(request.url.path) == "/v4/documents/upload"
        assert request.headers["content-type"].startswith("multipart/form-data;")
        body = request.read()
        assert b'filename="sample.pdf"' in body
        assert FIXTURES.joinpath("sample.pdf").read_bytes() in body
        assert b'name="session_id"' not in body
        assert b'{"valid_from":"2024-01-01T00:00:00Z"}' in body
        return httpx.Response(202, json={"revision_id": "rev-example"})

    with MesaV4Client() as client:
        client._client.close()
        client._client = httpx.Client(
            base_url="http://test", transport=httpx.MockTransport(receive)
        )
        result = client.add_file(
            FIXTURES / "sample.pdf",
            **fields("sample"),
            metadata={"valid_from": "2024-01-01T03:00:00+03:00"},
        )
    assert result["revision_id"] == "rev-example"


@pytest.mark.asyncio
async def test_async_sdk_add_file_and_pending_retry_via_text_route(span_runtime):
    async with span_runtime() as env:
        owner = env["owner"]
        async with AsyncMesaV4Client(base_url="http://test") as client:
            await client._client.aclose()
            client._client = owner
            result = await client.add_file(FIXTURES / "sample.pdf", **fields("upload"))
            assert result["chunk_count"] == 2

            duplicate = await client.add_document(
                **fields("upload"),
                content="The first page records the migration.\n\nThe third page provides the heliotrope warranty.",
            )
            assert duplicate["duplicate"] is True
            assert duplicate["mutation_ids"] == result["mutation_ids"]


@pytest.mark.parametrize("suffix", [".docx", ".xlsx", ".udf"])
def test_archive_that_expands_past_file_limit_is_rejected_before_parsing(suffix):
    import io
    import zipfile

    from mesa_api import document_files

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as out:
        out.writestr("content.xml", b" " * (document_files.MAX_FILE_BYTES + 1))
    assert archive.tell() < 1_000_000
    archive.seek(0)
    with (
        patch.object(document_files, "Document", side_effect=AssertionError),
        patch.object(document_files, "load_workbook", side_effect=AssertionError),
        patch.object(document_files, "_udf", side_effect=AssertionError),
        pytest.raises(document_files.DocumentFileError, match="expands"),
    ):
        document_files.extract_document_file(archive, f"bomb{suffix}")
