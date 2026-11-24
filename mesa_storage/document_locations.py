"""File coordinates associated with the extracted text of a revision."""

from bisect import bisect_right
from collections.abc import Iterator
from itertools import pairwise
from operator import itemgetter

# Consecutive segments: (exclusive character end, 1-based page/row, sheet or None).
DocumentLocations = list[tuple[int, int, str | None]]


def document_chunk_boundaries(locations: DocumentLocations) -> Iterator[int]:
    """Keep each chunk inside one PDF page or XLSX sheet."""
    for current, following in pairwise(locations):
        if current[2] is None or current[2] != following[2]:
            yield current[0]
    if locations:
        yield locations[-1][0]


def document_citation(
    locations: DocumentLocations, start: int, end: int
) -> dict[str, str | int]:
    """Map an existing half-open span to its PDF page or inclusive Excel rows."""
    if not locations or end <= start:
        return {}
    first = bisect_right(locations, start, key=itemgetter(0))
    last = bisect_right(locations, end - 1, key=itemgetter(0))
    if last >= len(locations):
        return {}
    _, first_number, sheet = locations[first]
    _, last_number, last_sheet = locations[last]
    if sheet is None:
        return {"page": first_number} if first == last else {}
    if sheet == last_sheet:
        return {"sheet": sheet, "row_start": first_number, "row_end": last_number}
    return {}
