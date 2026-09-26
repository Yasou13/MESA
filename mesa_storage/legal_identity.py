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
    "TBK": {
        "canonical": "Türk Borçlar Kanunu",
        "aliases": ["türk borçlar kanunu", "borçlar kanunu", "6098 sayılı türk borçlar kanunu", "6098 sayılı kanun", "6098", "tbk"],
    },
    "TMK": {
        "canonical": "Türk Medeni Kanunu",
        "aliases": ["türk medeni kanunu", "medeni kanun", "4721 sayılı türk medeni kanunu", "4721 sayılı kanun", "4721", "tmk", "m.k.", "mk"],
    },
    "TCK": {
        "canonical": "Türk Ceza Kanunu",
        "aliases": ["türk ceza kanunu", "ceza kanunu", "5237 sayılı türk ceza kanunu", "5237 sayılı kanun", "5237", "tck"],
    },
    "CMK": {
        "canonical": "Ceza Muhakemesi Kanunu",
        "aliases": ["ceza muhakemesi kanunu", "ceza muhakemeleri kanunu", "5271 sayılı ceza muhakemesi kanunu", "5271 sayılı kanun", "5271", "cmk", "cmuk"],
    },
    "HMK": {
        "canonical": "Hukuk Muhakemeleri Kanunu",
        "aliases": ["hukuk muhakemeleri kanunu", "hukuk usulü muhakemeleri kanunu", "6100 sayılı hukuk muhakemeleri kanunu", "6100 sayılı kanun", "6100", "hmk", "humk"],
    },
    "TTK": {
        "canonical": "Türk Ticaret Kanunu",
        "aliases": ["türk ticaret kanunu", "ticaret kanunu", "6102 sayılı türk ticaret kanunu", "6102 sayılı kanun", "6102", "ttk"],
    },
    "İYUK": {
        "canonical": "İdari Yargılama Usulü Kanunu",
        "aliases": ["idari yargılama usulü kanunu", "idari yargılama kanunu", "2577 sayılı idari yargılama usulü kanunu", "2577 sayılı kanun", "2577", "iyuk"],
    },
    "KVKK": {
        "canonical": "Kişisel Verilerin Korunması Kanunu",
        "aliases": ["kişisel verilerin korunması kanunu", "6698 sayılı kişisel verilerin korunması kanunu", "6698 sayılı kanun", "6698", "kvkk", "k.v.k.k."],
    },
    "İş Kanunu": {
        "canonical": "İş Kanunu",
        "aliases": ["iş kanunu", "türk iş kanunu", "4857 sayılı iş kanunu", "4857 sayılı kanun", "is kanunu", "4857 s.k.", "4857 sk", "4857"],
    },
    "Anayasa": {
        "canonical": "Anayasa",
        "aliases": ["türk anayasası", "türkiye cumhuriyeti anayasası", "t.c. anayasası", "1982 anayasası", "2709 sayılı kanun", "anayasa", "ay"],
    },
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
            rf"(?P<has_art_marker>m\.|md\.?|madde)?\s*(?P<art>\d+)(?:\.|\'[a-zçğıöşü]+)?(?:\s*madde(?:si)?)?(?:\s+(?:uyarınca|gereğince|kapsamında|hükmü|hükmünce|düzenlemesi))?\s+(?P<statute>{alias_pattern})(?:(?=\W)|$)",
            re.IGNORECASE,
        )

    def extract_citations(self, text: str) -> list[LegalCitation]:
        if not text:
            return []
        normalized = normalize_turkish(text)
        raw_matches: list[dict[str, Any]] = []

        # Find forward matches (statute [article])
        for match in self._forward_pattern.finditer(normalized):
            statute_match = match.group("statute")
            code = self.alias_to_code.get(statute_match)
            if not code:
                continue
            article = match.group("art")

            # Guard against short alias false positives (e.g. "ay" meaning month or digits)
            if statute_match in ("ay",):
                orig_snippet = text[match.start("statute"):match.end("statute")]
                is_explicit_upper = orig_snippet in ("AY", "A.Y.", "A. Y.")
                has_article = bool(article)
                if not (is_explicit_upper or has_article):
                    continue
            elif statute_match.isdigit():
                # Pure statute law numbers (e.g. "4857") MUST be accompanied by article or law keyword
                if not article and "sayılı" not in match.group(0):
                    continue

            raw_matches.append({
                "code": code,
                "article": article,
                "start": match.start(),
                "end": match.end(),
                "statute_match": statute_match,
            })

        # Find reverse matches ([article] statute)
        for match in self._reverse_pattern.finditer(normalized):
            statute_match = match.group("statute")
            code = self.alias_to_code.get(statute_match)
            if not code:
                continue
            article = match.group("art")
            has_marker = bool(match.group("has_art_marker") or "madde" in match.group(0))

            # Guard: bare numbers before "ay" (e.g. "3 ay") are durations, NOT Anayasa citations!
            if statute_match == "ay":
                orig_snippet = text[match.start("statute"):match.end("statute")]
                is_explicit_upper = orig_snippet in ("AY", "A.Y.", "A. Y.")
                if not (is_explicit_upper or has_marker):
                    continue
            elif statute_match.isdigit():
                if not has_marker and "sayılı" not in match.group(0):
                    continue

            raw_matches.append({
                "code": code,
                "article": article,
                "start": match.start(),
                "end": match.end(),
                "statute_match": statute_match,
            })

        if not raw_matches:
            return []

        # Semantic Deduplication:
        # If a match with an article overlaps with or subsumes a match without an article for the same statute,
        # keep the specific article citation (e.g. "117 TBK" -> TBK m.117, NOT both TBK and TBK m.117).
        filtered_matches: list[dict[str, Any]] = []
        for i, m1 in enumerate(raw_matches):
            is_subsumed = False
            for j, m2 in enumerate(raw_matches):
                if i == j:
                    continue
                if m1["code"] != m2["code"]:
                    continue
                # If m1 has no article but m2 has an article and their spans overlap or touch
                if m1["article"] is None and m2["article"] is not None:
                    # Check overlap or containment
                    if max(m1["start"], m2["start"]) <= min(m1["end"], m2["end"]):
                        is_subsumed = True
                        break
                # If both have the same article and overlap, keep the larger span
                if m1["article"] == m2["article"] and m1["article"] is not None:
                    if max(m1["start"], m2["start"]) < min(m1["end"], m2["end"]):
                        span1 = m1["end"] - m1["start"]
                        span2 = m2["end"] - m2["start"]
                        if span1 < span2 or (span1 == span2 and i > j):
                            is_subsumed = True
                            break
            if not is_subsumed:
                filtered_matches.append(m1)

        citations: list[LegalCitation] = []
        seen: set[tuple[str, str | None]] = set()
        for m in filtered_matches:
            key = (m["code"], m["article"])
            if key in seen:
                continue
            seen.add(key)
            info = self.ontology[m["code"]]
            article = m["article"]
            citations.append(
                LegalCitation(
                    statute_code=m["code"],
                    statute_canonical=info["canonical"],
                    article=article,
                    canonical_name=(
                        f"{m['code']} m.{article}"
                        if article and m["code"] in self.laws
                        else m["code"]
                    ),
                    aliases=tuple(info["aliases"]),
                )
            )
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
