"""
Canonical Fact Extraction Service for MESA MVP Round 5.

This module owns:
- ``FactCandidate`` contract: Strict structured representation of atomic facts.
- ``FactExtractionResponse``: Strict structured output schema.
- ``DeterministicFactValidator``: Deterministic schema, bounds, and deduplication checks (0 LLM calls).
- ``FactExtractionService``: Canonical extraction service with single-call model extraction
  and max 1 schema-correction retry.
- ``fact_candidates_to_extracted_triplet``: Safe compatibility mapping to existing ExtractedTriplet.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import config
from mesa_memory.consolidation.schemas import ExtractedTriplet

logger = logging.getLogger("MESA_FactExtraction")


class FactExtractionError(RuntimeError):
    """Raised when structured fact extraction fails after bounded retries."""


class FactCandidate(BaseModel):
    """Canonical extraction representation of a single extracted fact."""

    fact_text: str = Field(
        ..., min_length=1, description="Natural language statement of the fact"
    )
    subject: str = Field(..., min_length=1, description="Subject entity / concept")
    predicate: str = Field(..., min_length=1, description="Predicate / relation")
    object: str = Field(
        ..., min_length=1, description="Object entity / attribute value"
    )
    valid_from: Optional[str] = Field(
        default=None, description="ISO datetime or temporal anchor start"
    )
    valid_to: Optional[str] = Field(
        default=None, description="ISO datetime or temporal anchor end"
    )
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Extraction confidence score in [0.0, 1.0]",
    )
    source_span: Optional[str] = Field(
        default=None, description="Exact substring span from the source text"
    )
    supersedes: Optional[str] = Field(
        default=None,
        description="Identifier or content of previous fact superseded",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary semantic or provenance metadata"
    )

    @field_validator("fact_text", "subject", "predicate", "object", mode="before")
    @classmethod
    def strip_and_validate_nonempty(cls, v: Any) -> str:
        if v is None:
            raise ValueError("Field cannot be None")
        v_str = str(v).strip()
        if not v_str:
            raise ValueError("Field cannot be empty or whitespace only")
        return v_str

    @field_validator("source_span", "supersedes", "valid_from", "valid_to", mode="before")
    @classmethod
    def strip_optional_str(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        v_str = str(v).strip()
        return v_str if v_str else None

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, v: Any) -> Optional[float]:
        if v is None or v == "":
            return None
        try:
            v_float = float(v)
        except (ValueError, TypeError):
            raise ValueError(f"Confidence must be a valid float, got {v}")
        if not (0.0 <= v_float <= 1.0):
            raise ValueError(f"Confidence must be in range [0.0, 1.0], got {v_float}")
        return v_float


class FactExtractionResponse(BaseModel):
    """Root schema: zero or more extracted canonical facts."""

    facts: list[FactCandidate] = Field(
        default_factory=list,
        description="Zero or more extracted canonical facts",
    )


class DeterministicFactValidator:
    """Validates extracted FactCandidates deterministically without calling any LLMs."""

    @staticmethod
    def validate(candidate: FactCandidate, source_text: Optional[str] = None) -> bool:
        if (
            not candidate.fact_text
            or not candidate.subject
            or not candidate.predicate
            or not candidate.object
        ):
            return False
        if candidate.confidence is not None and not (
            0.0 <= candidate.confidence <= 1.0
        ):
            return False
        return True

    @staticmethod
    def deduplicate_and_canonicalize(
        candidates: list[FactCandidate],
        source_text: Optional[str] = None,
    ) -> list[FactCandidate]:
        """Filter invalid candidates and deduplicate by (subject, predicate, object, valid_from)."""
        valid: list[FactCandidate] = []
        seen: set[tuple[str, str, str, Optional[str]]] = set()
        for c in candidates:
            if not DeterministicFactValidator.validate(c, source_text=source_text):
                continue
            key = (
                c.subject.casefold(),
                c.predicate.casefold(),
                c.object.casefold(),
                c.valid_from,
            )
            if key in seen:
                continue
            seen.add(key)
            valid.append(c)
        return valid


def fact_candidates_to_extracted_triplet(
    candidates: list[FactCandidate], record_index: int = 0
) -> Optional[ExtractedTriplet]:
    """Map FactCandidates into existing ExtractedTriplet representation for downstream compatibility."""
    if not candidates:
        return None

    primary = candidates[0]
    additional = []
    for c in candidates[1:]:
        additional.append(
            {
                "head": c.subject,
                "relation": c.predicate,
                "tail": c.object,
                "confidence": c.confidence,
                "fact_text": c.fact_text,
                "valid_from": c.valid_from,
                "valid_to": c.valid_to,
                "supersedes": c.supersedes,
                "source_span": c.source_span,
                "metadata": c.metadata,
            }
        )

    return ExtractedTriplet(
        record_index=record_index,
        head=primary.subject,
        relation=primary.predicate,
        tail=primary.object,
        confidence=primary.confidence,
        additional_triplets=additional,
    )


EXTRACTION_PROMPT_TR = """Aşağıdaki metinden yapılandırılmış olguları (facts) çıkar.
Her olgu için şu alanları sağla:
- fact_text: Olgunun tam Türkçe ifadesi
- subject: Özne / Kavram / Varlık
- predicate: Yüklem / İlişki
- object: Nesne / Değer / Durum
- valid_from: Varsa başlangıç zamanı (ISO veya metindeki ifade), yoksa null
- valid_to: Varsa bitiş zamanı, yoksa null
- confidence: 0.0 ile 1.0 arasında güven puanı
- source_span: Metindeki ilgili kaynak ifade/cümle
- supersedes: Bu olgu önceki bir durumu/tercihi geçersiz kılıyorsa (düzeltme/güncelleme) neyi geçersiz kıldığı, yoksa null

Eğer metinde hiçbir somut olgu/tercih/durum yoksa (örneğin sadece selamlaşma, teşekkür, havadan sudan konuşma), facts listesini boş bırak: []

Metin:
{text}
"""

EXTRACTION_PROMPT_EN = """Extract structured facts from the following text.
For each fact, provide:
- fact_text: Complete natural language statement of the fact
- subject: Subject entity / concept
- predicate: Predicate / relation
- object: Object / attribute value / state
- valid_from: Valid from timestamp/date if mentioned, else null
- valid_to: Valid to timestamp/date if mentioned, else null
- confidence: Confidence score between 0.0 and 1.0
- source_span: Exact or approximate source phrase from text
- supersedes: What previous fact/preference this updates or supersedes, else null

If the text contains no factual statements or preferences (e.g. greetings, pleasantries, filler), return an empty facts list: []

Text:
{text}
"""

CORRECTION_PROMPT_TR = """Önceki yanıt geçerli bir JSON şemasına uymadı.
Hata: {error}

Lütfen metni tekrar inceleyip aşağıdaki şemaya kesinlikle uyan geçerli bir JSON döndür:
{{"facts": [{{"fact_text": "...", "subject": "...", "predicate": "...", "object": "...", "valid_from": null, "valid_to": null, "confidence": 1.0, "source_span": "...", "supersedes": null}}]}}

Orijinal Metin:
{text}
"""

CORRECTION_PROMPT_EN = """The previous output was not valid JSON conforming to the schema.
Error: {error}

Please re-extract structured facts conforming strictly to the schema:
{{"facts": [{{"fact_text": "...", "subject": "...", "predicate": "...", "object": "...", "valid_from": null, "valid_to": null, "confidence": 1.0, "source_span": "...", "supersedes": null}}]}}

Original Text:
{text}
"""


class FactExtractionService:
    """Canonical single-model fact extraction service for MESA V4.

    Enforces:
    1. Exactly one normal model extraction call.
    2. Bounded schema-correction retry (maximum 1 retry).
    3. Strict structured output validation (0..N FactCandidate).
    4. Deterministic post-extraction fact validation (0 validation LLMs).
    5. Model independence (works with any BaseUniversalLLMAdapter).
    6. Decoupled from validation policy and REBEL.
    """

    def __init__(
        self,
        llm: BaseUniversalLLMAdapter,
        *,
        rebel_enabled: bool = False,
        extraction_lang: str = "tr",
    ):
        self.llm = llm
        self.rebel_enabled = rebel_enabled
        self.extraction_lang = extraction_lang
        self.validator = DeterministicFactValidator()
        self._rebel_extractor = None

        if self.rebel_enabled:
            # Opt-in legacy / experimental REBEL only
            try:
                from mesa_memory.extraction.rebel_pipeline import RebelExtractor

                self._rebel_extractor = RebelExtractor()
            except Exception as exc:
                logger.warning(
                    "Optional RebelExtractor initialization failed: %s", exc
                )

    def _get_prompt(self, text: str) -> str:
        if self.extraction_lang == "tr":
            return EXTRACTION_PROMPT_TR.format(text=text)
        return EXTRACTION_PROMPT_EN.format(text=text)

    def _get_correction_prompt(self, text: str, error: str) -> str:
        if self.extraction_lang == "tr":
            return CORRECTION_PROMPT_TR.format(text=text, error=error)
        return CORRECTION_PROMPT_EN.format(text=text, error=error)

    def _parse_response(self, raw_output: Any) -> FactExtractionResponse:
        """Parse the raw response from LLM into FactExtractionResponse."""
        if isinstance(raw_output, FactExtractionResponse):
            return raw_output
        if hasattr(raw_output, "triplets") and not isinstance(raw_output, (dict, str)):
            raw_output = {
                "triplets": [
                    t.model_dump() if hasattr(t, "model_dump") else t.__dict__
                    for t in raw_output.triplets
                ]
            }
        if isinstance(raw_output, str):
            cleaned = raw_output.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            elif cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            try:
                parsed_data = json.loads(cleaned)
                return self._parse_dict_or_list(parsed_data)
            except Exception:
                return FactExtractionResponse.model_validate_json(cleaned)
        if isinstance(raw_output, (dict, list)):
            return self._parse_dict_or_list(raw_output)
        raise ValueError(f"Unexpected extraction response type: {type(raw_output)}")

    def _parse_dict_or_list(self, data: Any) -> FactExtractionResponse:
        if isinstance(data, list):
            facts = [self._to_fact_candidate(item) for item in data if item]
            return FactExtractionResponse(facts=[f for f in facts if f is not None])
        if isinstance(data, dict):
            if "facts" in data and isinstance(data["facts"], list):
                facts = [self._to_fact_candidate(item) for item in data["facts"] if item]
                return FactExtractionResponse(facts=[f for f in facts if f is not None])
            if "triplets" in data and isinstance(data["triplets"], list):
                facts = [self._to_fact_candidate(item) for item in data["triplets"] if item]
                return FactExtractionResponse(facts=[f for f in facts if f is not None])
            candidate = self._to_fact_candidate(data)
            return FactExtractionResponse(facts=[candidate] if candidate else [])
        raise ValueError(f"Cannot parse extraction response data of type {type(data)}")

    def _to_fact_candidate(self, item: Any) -> Optional[FactCandidate]:
        if isinstance(item, FactCandidate):
            return item
        if not isinstance(item, dict):
            return None
        s = item.get("subject") or item.get("head") or item.get("özne") or ""
        p = (
            item.get("predicate")
            or item.get("relation")
            or item.get("yüklem")
            or item.get("iliskisi")
            or ""
        )
        o = item.get("object") or item.get("tail") or item.get("nesne") or ""
        if not s or not p or not o:
            return None
        text = item.get("fact_text") or f"{s} {p} {o}".strip()
        additional = item.get("additional_triplets") or []
        extra_facts = []
        for add in additional:
            if isinstance(add, dict):
                as_s = add.get("subject") or add.get("head") or ""
                as_p = add.get("predicate") or add.get("relation") or ""
                as_o = add.get("object") or add.get("tail") or ""
                if as_s and as_p and as_o:
                    extra_facts.append(
                        {
                            "fact_text": add.get("fact_text")
                            or f"{as_s} {as_p} {as_o}".strip(),
                            "subject": as_s,
                            "predicate": as_p,
                            "object": as_o,
                            "confidence": add.get("confidence"),
                            "valid_from": add.get("valid_from"),
                            "valid_to": add.get("valid_to"),
                            "source_span": add.get("source_span"),
                            "supersedes": add.get("supersedes"),
                        }
                    )
        return FactCandidate(
            fact_text=text,
            subject=s,
            predicate=p,
            object=o,
            confidence=item.get("confidence"),
            valid_from=item.get("valid_from"),
            valid_to=item.get("valid_to"),
            source_span=item.get("source_span"),
            supersedes=item.get("supersedes"),
            metadata={"additional": extra_facts} if extra_facts else {},
        )

    def _expand_fact_candidates(
        self, candidates: list[FactCandidate]
    ) -> list[FactCandidate]:
        expanded: list[FactCandidate] = []
        for c in candidates:
            expanded.append(c)
            if c.metadata and "additional" in c.metadata:
                for add in c.metadata["additional"]:
                    try:
                        expanded.append(FactCandidate.model_validate(add))
                    except Exception:
                        pass
        return expanded

    async def extract_facts(
        self, text: str, *, source_ref: str | None = None
    ) -> list[FactCandidate]:
        """Extract 0..N canonical FactCandidates from text using exactly 1 normal model call.

        If schema validation fails, 1 correction retry is attempted.
        """
        if not text or not text.strip():
            return []

        prompt = self._get_prompt(text)
        loop = asyncio.get_running_loop()

        # Call 1: Normal structured extraction
        try:
            raw_response = await loop.run_in_executor(
                None,
                functools.partial(
                    self.llm.complete,
                    prompt,
                    FactExtractionResponse,
                ),
            )
            parsed_response = self._parse_response(raw_response)
        except Exception as first_exc:
            logger.warning(
                "Structured extraction attempt 1 failed; retrying with schema correction: %s",
                first_exc,
            )
            # Call 2: Single bounded correction retry
            correction_prompt = self._get_correction_prompt(text, str(first_exc))
            try:
                raw_retry = await loop.run_in_executor(
                    None,
                    functools.partial(
                        self.llm.complete,
                        correction_prompt,
                        FactExtractionResponse,
                    ),
                )
                parsed_response = self._parse_response(raw_retry)
            except Exception as second_exc:
                logger.error(
                    "Structured extraction correction retry failed: %s", second_exc
                )
                raise FactExtractionError(
                    f"Fact extraction failed after schema correction retry: {second_exc}"
                ) from second_exc

        all_candidates = self._expand_fact_candidates(parsed_response.facts)
        # Deterministic fact validation & deduplication
        valid_facts = self.validator.deduplicate_and_canonicalize(
            all_candidates, source_text=text
        )
        return valid_facts

    async def extract_facts_from_record(
        self, record: dict[str, Any]
    ) -> list[FactCandidate]:
        content = (
            record.get("content_payload")
            or record.get("content")
            or record.get("text", "")
        )
        source_ref = record.get("source_ref") or str(
            record.get("id") or record.get("raw_log_id", "")
        )
        return await self.extract_facts(content, source_ref=source_ref)

    async def extract_batch(
        self, sorted_batch: list[dict[str, Any]]
    ) -> tuple[dict[int, ExtractedTriplet], dict[int, ExtractedTriplet]]:
        """Extract facts across records and map to ExtractedTriplet dicts.

        In canonical V4, indexed_a and indexed_b contain the exact same canonical
        single-extraction results (no dual extraction!).
        """
        indexed: dict[int, ExtractedTriplet] = {}
        for idx, record in enumerate(sorted_batch):
            facts = await self.extract_facts_from_record(record)
            triplet = fact_candidates_to_extracted_triplet(facts, record_index=idx)
            if triplet is not None:
                indexed[idx] = triplet

        # Return identical mapping for both positions for downstream compatibility
        return indexed, {k: v.model_copy(deep=True) for k, v in indexed.items()}
