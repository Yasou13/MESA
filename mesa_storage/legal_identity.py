"""Canonical Turkish legal identity shared by storage and retrieval layers."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


def normalize_turkish(text: str) -> str:
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
    return unicodedata.normalize("NFKC", cleaned).lower()


@dataclass(frozen=True)
class LegalCitation:
    statute_code: str
    statute_canonical: str
    article: str | None
    canonical_name: str
    aliases: tuple[str, ...]
    jurisdiction: str = "TR"
    identity_version: str = "tr-legal-v1"


ONTOLOGY: dict[str, dict[str, Any]] = {
    "TBK": {"canonical": "Türk Borçlar Kanunu", "aliases": ["türk borçlar kanunu", "borçlar kanunu", "tbk"]},
    "TMK": {"canonical": "Türk Medeni Kanunu", "aliases": ["türk medeni kanunu", "medeni kanun", "tmk", "m.k.", "mk"]},
    "TCK": {"canonical": "Türk Ceza Kanunu", "aliases": ["türk ceza kanunu", "ceza kanunu", "tck"]},
    "CMK": {"canonical": "Ceza Muhakemesi Kanunu", "aliases": ["ceza muhakemesi kanunu", "ceza muhakemeleri kanunu", "cmk", "cmuk"]},
    "HMK": {"canonical": "Hukuk Muhakemeleri Kanunu", "aliases": ["hukuk muhakemeleri kanunu", "hukuk usulü muhakemeleri kanunu", "hmk", "humk"]},
    "TTK": {"canonical": "Türk Ticaret Kanunu", "aliases": ["türk ticaret kanunu", "ticaret kanunu", "ttk"]},
    "İYUK": {"canonical": "İdari Yargılama Usulü Kanunu", "aliases": ["idari yargılama usulü kanunu", "idari yargılama kanunu", "iyuk"]},
    "KVKK": {"canonical": "Kişisel Verilerin Korunması Kanunu", "aliases": ["kişisel verilerin korunması kanunu", "kvkk", "k.v.k.k."]},
    "İş Kanunu": {"canonical": "İş Kanunu", "aliases": ["iş kanunu", "4857 sayılı iş kanunu", "is kanunu", "4857 sayılı kanun"]},
    "Anayasa": {"canonical": "Anayasa", "aliases": ["türk anayasası", "türkiye cumhuriyeti anayasası", "t.c. anayasası", "anayasa", "ay"]},
    "Yargıtay Hukuk Genel Kurulu": {"canonical": "Yargıtay Hukuk Genel Kurulu", "aliases": ["yargıtay hukuk genel kurulu", "hukuk genel kurulu", "yhgk"]},
    "Yargıtay Ceza Genel Kurulu": {"canonical": "Yargıtay Ceza Genel Kurulu", "aliases": ["yargıtay ceza genel kurulu", "ceza genel kurulu", "ycgk"]},
    "Yargıtay 4. Hukuk Dairesi": {"canonical": "Yargıtay 4. Hukuk Dairesi", "aliases": ["yargıtay 4. hukuk dairesi", "yargıtay 4. dairesi", "4. hukuk dairesi", "4.hd", "4. hd"]},
    "Yargıtay 11. Hukuk Dairesi": {"canonical": "Yargıtay 11. Hukuk Dairesi", "aliases": ["yargıtay 11. hukuk dairesi", "yargıtay 11. dairesi", "11. hukuk dairesi", "11.hd", "11. hd"]},
}


class LegalEntityResolver:
    def __init__(self) -> None:
        self.ontology = ONTOLOGY
        self.laws = {"TBK", "TMK", "TCK", "CMK", "HMK", "TTK", "İYUK", "KVKK", "İş Kanunu", "Anayasa"}
        self.alias_to_code: dict[str, str] = {}
        for code, info in self.ontology.items():
            for alias in info["aliases"]:
                self.alias_to_code[normalize_turkish(alias)] = code
        aliases = sorted(self.alias_to_code, key=len, reverse=True)
        alias_pattern = "|".join(re.escape(alias) for alias in aliases)
        self._forward_pattern = re.compile(
            rf"(?:(?<=\W)|^)(?P<statute>{alias_pattern})(?:(?=\W)|$)(?:\s*(?:m\.|md\.?|madde))?\s*(?P<art>\d+)?(?:\.|\'[a-zçğıöşü]+)?(?:\s*madde(?:si)?)?",
            re.IGNORECASE,
        )
        self._reverse_pattern = re.compile(
            rf"(?:m\.|md\.?|madde)?\s*(?P<art>\d+)(?:\.|\'[a-zçğıöşü]+)?(?:\s*madde(?:si)?)?(?:\s+(?:uyarınca|gereğince|kapsamında|hükmü|hükmünce|düzenlemesi))?\s+(?P<statute>{alias_pattern})(?:(?=\W)|$)",
            re.IGNORECASE,
        )

    def extract_citations(self, text: str) -> list[LegalCitation]:
        if not text:
            return []
        normalized = normalize_turkish(text)
        citations: list[LegalCitation] = []
        seen: set[tuple[str, str | None]] = set()
        for pattern in (self._forward_pattern, self._reverse_pattern):
            for match in pattern.finditer(normalized):
                code = self.alias_to_code.get(match.group("statute"))
                if not code:
                    continue
                article = match.group("art")
                key = (code, article)
                if key in seen:
                    continue
                seen.add(key)
                info = self.ontology[code]
                citations.append(LegalCitation(
                    statute_code=code,
                    statute_canonical=info["canonical"],
                    article=article,
                    canonical_name=f"{code} m.{article}" if article and code in self.laws else code,
                    aliases=tuple(info["aliases"]),
                ))
        return citations

    def canonicalize_entity(self, text: str) -> str:
        citations = self.extract_citations(text)
        if len(citations) != 1:
            return text
        citation = citations[0]
        return (
            citation.canonical_name
            if citation.article
            else citation.statute_canonical
        )

    def extract_entities(self, text: str) -> list[str]:
        entities: list[str] = []
        for citation in self.extract_citations(text):
            if citation.canonical_name not in entities:
                entities.append(citation.canonical_name)
            if citation.statute_code not in entities:
                entities.append(citation.statute_code)
        return entities
