"""Ontology-based entity and citation resolution for Turkish Legal Domain."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


def normalize_turkish(text: str) -> str:
    """Deterministic, Turkish-aware lowercasing preserving dotted and dotless I."""
    if not text:
        return ""
    cleaned = unicodedata.normalize("NFC", text)
    cleaned = (
        cleaned.replace("İ", "i")
        .replace("I", "ı")
        .replace("\u0130", "i")
        .replace("\u0131", "ı")
        .replace("i\u0307", "i")
    )
    cleaned = unicodedata.normalize("NFKC", cleaned)
    return cleaned.lower()


@dataclass(frozen=True)
class LegalCitation:
    """Represents a structured canonical legal citation."""

    statute_code: str
    statute_canonical: str
    article: str | None
    canonical_name: str
    aliases: tuple[str, ...]


ONTOLOGY: dict[str, dict[str, Any]] = {
    # Laws
    "TBK": {
        "canonical": "Türk Borçlar Kanunu",
        "aliases": ["türk borçlar kanunu", "borçlar kanunu", "tbk", "b.k.", "bk"],
    },
    "TMK": {
        "canonical": "Türk Medeni Kanunu",
        "aliases": ["türk medeni kanunu", "medeni kanun", "tmk", "m.k.", "mk"],
    },
    "TCK": {
        "canonical": "Türk Ceza Kanunu",
        "aliases": ["türk ceza kanunu", "ceza kanunu", "tck"],
    },
    "CMK": {
        "canonical": "Ceza Muhakemesi Kanunu",
        "aliases": [
            "ceza muhakemesi kanunu",
            "ceza muhakemeleri kanunu",
            "cmk",
            "cmuk",
        ],
    },
    "HMK": {
        "canonical": "Hukuk Muhakemeleri Kanunu",
        "aliases": [
            "hukuk muhakemeleri kanunu",
            "hukuk usulü muhakemeleri kanunu",
            "hmk",
            "humk",
        ],
    },
    "TTK": {
        "canonical": "Türk Ticaret Kanunu",
        "aliases": ["türk ticaret kanunu", "ticaret kanunu", "ttk"],
    },
    "İYUK": {
        "canonical": "İdari Yargılama Usulü Kanunu",
        "aliases": [
            "idari yargılama usulü kanunu",
            "idari yargılama kanunu",
            "iyuk",
        ],
    },
    "KVKK": {
        "canonical": "Kişisel Verilerin Korunması Kanunu",
        "aliases": [
            "kişisel verilerin korunması kanunu",
            "kvkk",
            "k.v.k.k.",
        ],
    },
    "İş Kanunu": {
        "canonical": "İş Kanunu",
        "aliases": [
            "iş kanunu",
            "4857 sayılı iş kanunu",
            "is kanunu",
            "4857 sayılı kanun",
        ],
    },
    "Anayasa": {
        "canonical": "Anayasa",
        "aliases": [
            "türk anayasası",
            "türkiye cumhuriyeti anayasası",
            "t.c. anayasası",
            "anayasa",
            "ay",
        ],
    },
    # Courts
    "Yargıtay Hukuk Genel Kurulu": {
        "canonical": "Yargıtay Hukuk Genel Kurulu",
        "aliases": [
            "yargıtay hukuk genel kurulu",
            "hukuk genel kurulu",
            "yhgk",
        ],
    },
    "Yargıtay Ceza Genel Kurulu": {
        "canonical": "Yargıtay Ceza Genel Kurulu",
        "aliases": [
            "yargıtay ceza genel kurulu",
            "ceza genel kurulu",
            "ycgk",
        ],
    },
    "Yargıtay 4. Hukuk Dairesi": {
        "canonical": "Yargıtay 4. Hukuk Dairesi",
        "aliases": [
            "yargıtay 4. hukuk dairesi",
            "yargıtay 4. dairesi",
            "4. hukuk dairesi",
            "4.hd",
            "4. hd",
        ],
    },
    "Yargıtay 11. Hukuk Dairesi": {
        "canonical": "Yargıtay 11. Hukuk Dairesi",
        "aliases": [
            "yargıtay 11. hukuk dairesi",
            "yargıtay 11. dairesi",
            "11. hukuk dairesi",
            "11.hd",
            "11. hd",
        ],
    },
}


class LegalEntityResolver:
    """Ontology-based entity resolution for Turkish Legal Domain.

    Extracts canonical law names and courts from text, associating articles
    strictly with their proximal statute to prevent Cartesian collisions
    (e.g., 'TBK 117 ve TMK 50' maps 117 to TBK and 50 to TMK only).
    """

    def __init__(self) -> None:
        self.ontology = ONTOLOGY
        self.laws = {
            "TBK",
            "TMK",
            "TCK",
            "CMK",
            "HMK",
            "TTK",
            "İYUK",
            "KVKK",
            "İş Kanunu",
            "Anayasa",
        }

        # Build alias -> code mapping with normalized forms
        self.alias_to_code: dict[str, str] = {}
        for code, info in self.ontology.items():
            for alias in info["aliases"]:
                norm_alias = normalize_turkish(alias)
                self.alias_to_code[norm_alias] = code

        # Sort aliases by length descending so longer phrases match first
        sorted_aliases = sorted(self.alias_to_code.keys(), key=len, reverse=True)
        alias_pattern = "|".join(re.escape(a) for a in sorted_aliases)

        # Forward citation: <statute> ... <article>
        self._forward_pattern = re.compile(
            rf"(?:(?<=\W)|^)(?P<statute>{alias_pattern})(?:(?=\W)|$)"
            rf"(?:\s*(?:m\.|md\.?|madde))?\s*(?P<art>\d+)?(?:\.|\'[a-zçğıöşü]+)?(?:\s*madde(?:si)?)?",
            re.IGNORECASE,
        )

        # Reverse citation: <article> ... <statute>
        self._reverse_pattern = re.compile(
            rf"(?:m\.|md\.?|madde)?\s*(?P<art>\d+)(?:\.|\'[a-zçğıöşü]+)?(?:\s*madde(?:si)?)?"
            rf"(?:\s+(?:uyarınca|gereğince|kapsamında|hükmü|hükmünce|düzenlemesi))?"
            rf"\s+(?P<statute>{alias_pattern})(?:(?=\W)|$)",
            re.IGNORECASE,
        )

    def extract_citations(self, text: str) -> list[LegalCitation]:
        """Extract structured legal citations with proximal statute-article binding."""
        if not text:
            return []
        norm = normalize_turkish(text)
        citations: list[LegalCitation] = []
        seen_keys: set[tuple[str, str | None]] = set()

        # 1. Forward citations
        for m in self._forward_pattern.finditer(norm):
            statute_match = m.group("statute")
            code = self.alias_to_code.get(statute_match)
            if not code:
                continue
            art = m.group("art")
            info = self.ontology[code]
            key = (code, art)
            if key not in seen_keys:
                seen_keys.add(key)
                canonical_name = f"{code} m.{art}" if (art and code in self.laws) else code
                citations.append(
                    LegalCitation(
                        statute_code=code,
                        statute_canonical=info["canonical"],
                        article=art,
                        canonical_name=canonical_name,
                        aliases=tuple(info["aliases"]),
                    )
                )

        # 2. Reverse citations
        for m in self._reverse_pattern.finditer(norm):
            statute_match = m.group("statute")
            code = self.alias_to_code.get(statute_match)
            if not code:
                continue
            art = m.group("art")
            info = self.ontology[code]
            key = (code, art)
            if key not in seen_keys:
                seen_keys.add(key)
                canonical_name = f"{code} m.{art}" if (art and code in self.laws) else code
                citations.append(
                    LegalCitation(
                        statute_code=code,
                        statute_canonical=info["canonical"],
                        article=art,
                        canonical_name=canonical_name,
                        aliases=tuple(info["aliases"]),
                    )
                )

        # Clean duplicates where base statute is superseded by article-level citation
        article_statutes = {c.statute_code for c in citations if c.article}
        pruned: list[LegalCitation] = []
        for c in citations:
            pruned.append(c)

        return pruned

    def extract_entities(self, text: str) -> list[str]:
        """Extract canonical entity names suitable for graph seed lookup and ranking.

        Returns both base statute and article-level representation (e.g. 'TBK', 'TBK m.117').
        """
        citations = self.extract_citations(text)
        entities: list[str] = []
        for c in citations:
            if c.canonical_name not in entities:
                entities.append(c.canonical_name)
            if c.statute_code not in entities:
                entities.append(c.statute_code)
        return entities
