"""Canonical Turkish legal identity shared by storage and retrieval layers."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
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


def normalize_law_name(text: str) -> str:
    """Normalize exact store names, including the conventional Kanunu abbreviation."""
    text = normalize_turkish(text).replace("ı", "i")
    return " ".join(re.sub(r"\bk\.(?=\W|$)", "kanunu", text).split())


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
        "aliases": [
            "türk borçlar kanunu",
            "borçlar kanunu",
            "6098 sayılı türk borçlar kanunu",
            "6098 sayılı kanun",
            "6098",
            "tbk",
        ],
    },
    "TMK": {
        "canonical": "Türk Medeni Kanunu",
        "aliases": [
            "türk medeni kanunu",
            "medeni kanun",
            "4721 sayılı türk medeni kanunu",
            "4721 sayılı kanun",
            "4721",
            "tmk",
            "m.k.",
            "mk",
        ],
    },
    "TCK": {
        "canonical": "Türk Ceza Kanunu",
        "aliases": [
            "türk ceza kanunu",
            "ceza kanunu",
            "5237 sayılı türk ceza kanunu",
            "5237 sayılı kanun",
            "5237",
            "tck",
        ],
    },
    "CMK": {
        "canonical": "Ceza Muhakemesi Kanunu",
        "aliases": [
            "ceza muhakemesi kanunu",
            "ceza muhakemeleri kanunu",
            "5271 sayılı ceza muhakemesi kanunu",
            "5271 sayılı kanun",
            "5271",
            "cmk",
            "cmuk",
        ],
    },
    "HMK": {
        "canonical": "Hukuk Muhakemeleri Kanunu",
        "aliases": [
            "hukuk muhakemeleri kanunu",
            "hukuk usulü muhakemeleri kanunu",
            "6100 sayılı hukuk muhakemeleri kanunu",
            "6100 sayılı kanun",
            "6100",
            "hmk",
            "humk",
        ],
    },
    "TTK": {
        "canonical": "Türk Ticaret Kanunu",
        "aliases": [
            "türk ticaret kanunu",
            "ticaret kanunu",
            "6102 sayılı türk ticaret kanunu",
            "6102 sayılı kanun",
            "6102",
            "ttk",
        ],
    },
    "İYUK": {
        "canonical": "İdari Yargılama Usulü Kanunu",
        "aliases": [
            "idari yargılama usulü kanunu",
            "idari yargılama kanunu",
            "2577 sayılı idari yargılama usulü kanunu",
            "2577 sayılı kanun",
            "2577",
            "iyuk",
            "ıyuk",
        ],
    },
    "KVKK": {
        "canonical": "Kişisel Verilerin Korunması Kanunu",
        "aliases": [
            "kişisel verilerin korunması kanunu",
            "6698 sayılı kişisel verilerin korunması kanunu",
            "6698 sayılı kanun",
            "6698",
            "kvkk",
            "k.v.k.k.",
        ],
    },
    "İş Kanunu": {
        "canonical": "İş Kanunu",
        "aliases": [
            "iş kanunu",
            "türk iş kanunu",
            "4857 sayılı iş kanunu",
            "4857 sayılı kanun",
            "is kanunu",
            "4857 s.k.",
            "4857 sk",
            "4857",
        ],
    },
    "Anayasa": {
        "canonical": "Anayasa",
        "aliases": [
            "türk anayasası",
            "türkiye cumhuriyeti anayasası",
            "t.c. anayasası",
            "1982 anayasası",
            "2709 sayılı kanun",
            "anayasa",
            "ay",
        ],
    },
    "Yargıtay Hukuk Genel Kurulu": {
        "canonical": "Yargıtay Hukuk Genel Kurulu",
        "aliases": ["yargıtay hukuk genel kurulu", "hukuk genel kurulu", "yhgk"],
    },
    "Yargıtay Ceza Genel Kurulu": {
        "canonical": "Yargıtay Ceza Genel Kurulu",
        "aliases": ["yargıtay ceza genel kurulu", "ceza genel kurulu", "ycgk"],
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

LAW_IDS: dict[str, str] = {
    "TBK": "6098",
    "TMK": "4721",
    "TCK": "5237",
    "CMK": "5271",
    "HMK": "6100",
    "TTK": "6102",
    "İYUK": "2577",
    "KVKK": "6698",
    "İş Kanunu": "4857",
    "Anayasa": "2709",
}

_LEGAL_COMPONENT_PATTERN = re.compile(r"^[0-9A-Za-zÇĞİÖŞÜçğıöşü._:/-]{1,128}$")
_ARTICLE_PATTERN = re.compile(r"^(?:madde[- ]*)?(?P<article>\d+[A-Za-zÇĞİÖŞÜçğıöşü]?)$")


def canonicalize_trusted_legal_identity(
    value: Mapping[str, Any] | None,
) -> dict[str, str] | None:
    """Validate a runtime-owned legal identity snapshot and canonicalize aliases.

    This function is intentionally strict.  A partial, unknown or internally
    inconsistent identity is rejected instead of being guessed from a bare
    article number.
    """
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("legal identity must be an object")

    resolver = LegalEntityResolver()
    law_ref = str(value.get("law_id") or "").strip()
    statute_ref = str(value.get("statute_code") or "").strip()
    article_ref = str(value.get("article_id") or "").strip()
    if not law_ref or not article_ref:
        raise ValueError("legal identity requires law_id and article_id")

    def resolve_code(reference: str) -> str | None:
        if re.fullmatch(r"[0-9]{3,5}", reference):
            return next(
                (code for code, number in LAW_IDS.items() if number == reference),
                reference,
            )
        code = resolver.alias_to_code.get(normalize_turkish(reference))
        return code if code in resolver.laws else None

    law_code = resolve_code(law_ref)
    statute_code = resolve_code(statute_ref) if statute_ref else law_code
    if law_code is None or statute_code != law_code:
        raise ValueError("legal identity law aliases are unknown or inconsistent")

    article_match = _ARTICLE_PATTERN.fullmatch(article_ref)
    if article_match is None:
        raise ValueError("legal identity article_id is invalid")

    source = str(value.get("identity_source") or "catalog").strip()
    if source not in {"catalog", "verified_ingestion"}:
        raise ValueError("legal identity source is not trusted")

    normalized: dict[str, str] = {
        "law_id": LAW_IDS.get(law_code, law_code),
        "statute_code": law_code,
        "article_id": article_match.group("article"),
        "version_id": str(value.get("version_id") or "").strip(),
        "paragraph_id": str(value.get("paragraph_id") or "").strip(),
        "clause_id": str(value.get("clause_id") or "").strip(),
        "identity_source": source,
    }
    for key in ("version_id", "paragraph_id", "clause_id"):
        component = normalized[key]
        if component and _LEGAL_COMPONENT_PATTERN.fullmatch(component) is None:
            raise ValueError(f"legal identity {key} is invalid")
    return normalized


class LegalEntityResolver:
    def __init__(self, *, law_names: Mapping[str, str] | None = None) -> None:
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
        self.alias_to_code: dict[str, str] = {}
        self._law_names = {
            normalize_law_name(name): number
            for name, number in (law_names or {}).items()
            if name and re.fullmatch(r"[0-9]{3,5}", number)
        }
        name_patterns = []
        for name in sorted(self._law_names, key=len, reverse=True):
            escaped = re.escape(name)
            if name.endswith("kanunu"):
                escaped = re.escape(name[:-1]) + r"u?"
            name_patterns.append(escaped)
        self._scope_name_pattern = (
            re.compile(
                r"(?<!\w)(?P<name>" + "|".join(name_patterns) + r")"
                r"(?:['’]?(?:nun|un|nin|in))?(?!\w)"
                r"(?:\s{0,128}+(?:(?:m\.|md\.?|madde)\s{0,128}+(?P<marked>[0-9]{1,12}+(?![0-9]))"
                r"|(?P<ordinal>[0-9]{1,12}+(?![0-9]))(?:\.|\s{1,128}+(?:inci|uncu|üncü|nci|ncu|ncü))?\s{1,128}+madde(?:si)?))?"
            )
            if name_patterns
            else None
        )
        for code, info in self.ontology.items():
            for alias in info["aliases"]:
                self.alias_to_code[normalize_turkish(alias)] = code
        aliases = sorted(self.alias_to_code, key=len, reverse=True)
        alias_pattern = "|".join(re.escape(alias) for alias in aliases)
        # A legal token has at most 12 digits and each layout gap at most 128
        # characters. Possessive runs cannot backtrack or parse a digit suffix
        # as a different article. Long input is still scanned in full.
        self._forward_pattern = re.compile(
            rf"(?:(?<=\W)|^)(?P<statute>{alias_pattern})(?:(?=\W)|$)"
            rf"(?P<possessive>['’]n?[ıiuü]n)?"
            rf"\s{{0,128}}+(?:(?P<forward_art_marker>m\.|md\.?|madde)\s{{0,128}}+)?"
            rf"(?P<art>\d{{1,12}}+(?!\d))?(?:\.|\'[a-zçğıöşü]{{1,12}}+)?(?:\s{{0,128}}+madde(?:si)?)?",
            re.IGNORECASE,
        )
        self._reverse_pattern = re.compile(
            rf"(?P<has_art_marker>m\.|md\.?|madde)?\s{{0,128}}+(?<!\d)(?P<art>\d{{1,12}}+(?!\d))(?:\.|\'[a-zçğıöşü]{{1,12}}+)?(?:\s{{0,128}}+madde(?:si)?)?(?:\s{{1,128}}+(?:uyarınca|gereğince|kapsamında|hükmü|hükmünce|düzenlemesi))?\s{{1,128}}+(?P<statute>{alias_pattern})(?:(?=\W)|$)",
            re.IGNORECASE,
        )
        self._continued_article_pattern = re.compile(
            r"\s{0,128}+(?:(?:-|–|—|ila)\s{0,128}+|(?:,|;|ve|ile)\s{0,128}+(?:m\.|md\.?|madde)\s{0,128}+)"
            r"(?P<art>\d{1,12}+(?!\d))(?:\.|\'[a-zçğıöşü]{1,12}+)?(?:\s{0,128}+madde(?:si)?)?",
            re.IGNORECASE,
        )
        self._numbered_law_pattern = re.compile(
            r"\b(?P<number>[0-9]{3,5})\s{1,128}+sayılı\s{1,128}+"
            r"(?:[^\n.;!?]{0,160}?(?<=\S)\s{1,128}+)?kanun(?:u)?(?:['’]?(?:nun|un|nın|ın))?\s{1,128}+"
            r"(?:(?:m\.|md\.?|madde)\s{0,128}+(?P<marked>[0-9]{1,12}+(?![0-9]))"
            r"|(?P<ordinal>[0-9]{1,12}+(?![0-9]))(?:\.|\s{1,128}+(?:ıncı|inci|uncu|üncü|nci|ncı|ncu|ncü))?\s{1,128}+madde(?:si)?)",
        )
        self._duration_pattern = re.compile(
            r"\s{0,128}+(?:aylık|yıllık|günlük|haftalık|saatlik|dakikalık|"
            r"ay|yıl|gün|hafta|saat|dakika)(?:\W|$)"
        )

    def extract_citations(self, text: str) -> list[LegalCitation]:
        if not text:
            return []
        # Mask court mentions before both store-name and ontology matching.
        # Keep offsets and independent Constitution/article citations intact.
        text = re.sub(
            r"\banayasa\s+mahkemesi\w*|\baym\b",
            lambda match: " " * len(match[0]),
            text,
            flags=re.IGNORECASE,
        )
        if self._scope_name_pattern is not None:
            normalized_name_text = normalize_law_name(text)
            matches = list(self._scope_name_pattern.finditer(normalized_name_text))
            if matches:
                scoped_citations = []
                # Name normalization folds Turkish letters, expands K. and
                # collapses whitespace. Map its spans back before masking so
                # independent citations retain their spelling and grammar.
                source_text = normalize_turkish(text)
                source_spans: list[tuple[int, int]] = []
                for piece in re.finditer(
                    r"\s+|\bk\.(?=\W|$)|.", source_text, re.DOTALL
                ):
                    value = piece[0]
                    if value.isspace():
                        if not source_spans:
                            continue
                        width = 1
                    else:
                        width = len("kanunu") if value == "k." else 1
                    source_spans.extend([piece.span()] * width)
                if source_spans and source_text[-1].isspace():
                    source_spans.pop()
                remainder = list(source_text)
                for match in matches:
                    name = match["name"]
                    number = self._law_names.get(name)
                    if number is None:
                        number = self._law_names.get(name + "u")
                    if number is None:
                        continue
                    scoped_code = next(
                        (c for c, n in LAW_IDS.items() if n == number), number
                    )
                    scoped_article = match["marked"] or match["ordinal"]
                    scoped_citations.append(
                        LegalCitation(
                            statute_code=scoped_code,
                            statute_canonical=name,
                            article=scoped_article,
                            canonical_name=f"{scoped_code} m.{scoped_article}"
                            if scoped_article
                            else scoped_code,
                            aliases=(name,),
                        )
                    )
                    start = source_spans[match.start()][0]
                    end = source_spans[match.end() - 1][1]
                    remainder[start:end] = " " * (end - start)
                # A store name consumes its entire span before ontology matching,
                # so a shorter ontology alias cannot reinterpret that same name.
                scoped_citations.extend(
                    LegalEntityResolver().extract_citations("".join(remainder))
                )
                return list(dict.fromkeys(scoped_citations))
        normalized = normalize_turkish(text)
        raw_matches: list[dict[str, Any]] = []

        # Numbered statutes use the same typed candidate lane as ontology aliases.
        numbered_matches = list(self._numbered_law_pattern.finditer(normalized))
        for match in numbered_matches:
            number = match.group("number")
            code = next((code for code, n in LAW_IDS.items() if n == number), number)
            raw_matches.append(
                {
                    "code": code,
                    "article": match.group("marked") or match.group("ordinal"),
                    "start": match.start(),
                    "end": match.end(),
                    "statute_match": number,
                    "article_span": match.span("marked")
                    if match.group("marked")
                    else match.span("ordinal"),
                }
            )

        # Both streams advance left to right; never re-scan numbered matches.
        numbered_index = 0
        article_codes: dict[tuple[int, int], set[str]] = {}

        # Find forward matches (statute [article])
        for match in self._forward_pattern.finditer(normalized):
            while (
                numbered_index < len(numbered_matches)
                and numbered_matches[numbered_index].end() <= match.start()
            ):
                numbered_index += 1
            if (
                numbered_index < len(numbered_matches)
                and numbered_matches[numbered_index].start() <= match.start()
            ):
                continue
            statute_match = match.group("statute")
            code = self.alias_to_code.get(statute_match)
            if not code:
                continue
            article = match.group("art")
            if (
                article
                and match.group("possessive")
                and not match.group("forward_art_marker")
                and self._duration_pattern.match(normalized, match.end())
            ):
                article = None

            # Guard against short alias false positives (e.g. "ay" meaning month or digits)
            if statute_match in ("ay",):
                orig_snippet = text[match.start("statute") : match.end("statute")]
                is_explicit_upper = orig_snippet in ("AY", "A.Y.", "A. Y.")
                article_prefix = (
                    normalized[match.end("statute") : match.start("art")]
                    if article
                    else ""
                )
                has_article_marker = bool(re.search(r"m\.|md\.?|madde", article_prefix))
                if not (is_explicit_upper or has_article_marker):
                    continue
            elif statute_match.isdigit():
                # Pure statute law numbers (e.g. "4857") MUST be accompanied by article or law keyword
                if not article and "sayılı" not in match.group(0):
                    continue

            raw_matches.append(
                {
                    "code": code,
                    "article": article,
                    "start": match.start(),
                    "end": match.end(),
                    "statute_match": statute_match,
                    "article_span": match.span("art") if article else None,
                }
            )
            if article and code in self.laws:
                continuation_start = match.end()
                while continued := self._continued_article_pattern.match(
                    normalized, continuation_start
                ):
                    raw_matches.append(
                        {
                            "code": code,
                            "article": continued.group("art"),
                            "start": continued.start(),
                            "end": continued.end(),
                            "statute_match": statute_match,
                            "article_span": continued.span("art"),
                        }
                    )
                    continuation_start = continued.end()

        for item in raw_matches:
            if item.get("article_span") is not None:
                article_codes.setdefault(item["article_span"], set()).add(item["code"])

        # Find reverse matches ([article] statute)
        for match in self._reverse_pattern.finditer(normalized):
            statute_match = match.group("statute")
            code = self.alias_to_code.get(statute_match)
            if not code:
                continue
            article = match.group("art")
            has_marker = bool(
                match.group("has_art_marker") or "madde" in match.group(0)
            )

            # A number already attached to a preceding statute cannot also
            # become the next statute's reverse citation (TBK 117 CMK 86).
            if article_codes.get(match.span("art"), set()) - {code}:
                continue

            # Guard: bare numbers before "ay" (e.g. "3 ay") are durations, NOT Anayasa citations!
            if statute_match == "ay":
                orig_snippet = text[match.start("statute") : match.end("statute")]
                is_explicit_upper = orig_snippet in ("AY", "A.Y.", "A. Y.")
                if not (is_explicit_upper or has_marker):
                    continue
            elif statute_match.isdigit():
                if not has_marker and "sayılı" not in match.group(0):
                    continue

            raw_matches.append(
                {
                    "code": code,
                    "article": article,
                    "start": match.start(),
                    "end": match.end(),
                    "statute_match": statute_match,
                }
            )

        if not raw_matches:
            return []

        # Bucket by source offset to preserve a linear sweep (no match sorting).
        # All raw patterns have bounded spans. At any offset only matches from
        # that fixed-size window can overlap, so the active set is bounded too.
        # Keep the original raw order and the old strict/touching overlap rules.
        starts: list[list[int]] = [[] for _ in range(len(normalized) + 1)]
        for index, item in enumerate(raw_matches):
            starts[item["start"]].append(index)
        subsumed: set[int] = set()
        active: list[int] = []
        for offset, indexes in enumerate(starts):
            if not indexes:
                continue
            active = [i for i in active if raw_matches[i]["end"] >= offset]
            for index in indexes:
                item = raw_matches[index]
                for prior_index in active:
                    prior = raw_matches[prior_index]
                    if item["code"] != prior["code"]:
                        continue
                    if item["article"] is None and prior["article"] is not None:
                        subsumed.add(index)
                    elif prior["article"] is None and item["article"] is not None:
                        subsumed.add(prior_index)
                    elif (
                        item["article"] is not None
                        and item["article"] == prior["article"]
                        and offset < prior["end"]
                    ):
                        priority = (item["end"] - offset, -index)
                        prior_priority = (prior["end"] - prior["start"], -prior_index)
                        subsumed.add(
                            prior_index if priority > prior_priority else index
                        )
                active.append(index)
        filtered_matches = [m for i, m in enumerate(raw_matches) if i not in subsumed]

        citations: list[LegalCitation] = []
        seen: set[tuple[str, str | None]] = set()
        for m in filtered_matches:
            key = (m["code"], m["article"])
            if key in seen:
                continue
            seen.add(key)
            info = self.ontology.get(
                m["code"],
                {"canonical": f"{m['code']} sayılı Kanun", "aliases": [m["code"]]},
            )
            article = m["article"]
            citations.append(
                LegalCitation(
                    statute_code=m["code"],
                    statute_canonical=info["canonical"],
                    article=article,
                    canonical_name=(
                        f"{m['code']} m.{article}"
                        if article and (m["code"] in self.laws or m["code"].isdigit())
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
            citation.canonical_name if citation.article else citation.statute_canonical
        )

    def extract_entities(self, text: str) -> list[str]:
        entities: list[str] = []
        for citation in self.extract_citations(text):
            if citation.canonical_name not in entities:
                entities.append(citation.canonical_name)
            if citation.statute_code not in entities:
                entities.append(citation.statute_code)
        return entities
