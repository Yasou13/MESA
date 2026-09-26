"""Independent citation span and Turkish normalization regressions."""

import pytest

from mesa_storage.legal_identity import LegalEntityResolver


@pytest.mark.parametrize(
    "text", ["3 ay 15 gün", "6 ay 24 saat", "aday", "yapay", "onay"]
)
def test_duration_and_embedded_alias_are_not_legal_citations(text):
    assert LegalEntityResolver().extract_citations(text) == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("TBK 117 CMK 86", {("TBK", "117"), ("CMK", "86")}),
        ("TBK 117\nTMK 118", {("TBK", "117"), ("TMK", "118")}),
        ("117 TBK", {("TBK", "117")}),
        ("IYUK 24", {("İYUK", "24")}),
        ("İYUK 24", {("İYUK", "24")}),
        ("ıyuk 24", {("İYUK", "24")}),
        ("iyuk 24", {("İYUK", "24")}),
    ],
)
def test_legal_citation_spans_do_not_borrow_neighbor_articles(text, expected):
    assert {
        (c.statute_code, c.article)
        for c in LegalEntityResolver().extract_citations(text)
    } == expected


