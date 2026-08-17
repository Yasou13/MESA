from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import config
from mesa_memory.extraction.service import (
    DeterministicFactValidator,
    FactCandidate,
    FactExtractionError,
    FactExtractionResponse,
    FactExtractionService,
    fact_candidates_to_extracted_triplet,
)


class MockExtractionAdapter(BaseUniversalLLMAdapter):
    def __init__(self, responses: list[Any] | None = None):
        self.responses = list(responses or [])
        self.complete_count = 0
        self.prompts: list[str] = []

    def complete(self, prompt: str, schema=None, **kwargs):
        self.complete_count += 1
        self.prompts.append(prompt)
        if self.responses:
            resp = self.responses.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return resp
        return FactExtractionResponse(facts=[])

    async def acomplete(self, prompt: str, schema=None, **kwargs):
        return self.complete(prompt, schema, **kwargs)

    def embed(self, text: str, **kwargs) -> list[float]:
        return [0.1] * 768

    async def aembed(self, text: str, **kwargs) -> list[float]:
        return [0.1] * 768

    def embed_batch(self, texts: list[str], **kwargs) -> list[list[float]]:
        return [[0.1] * 768 for _ in texts]

    async def aembed_batch(self, texts: list[str], **kwargs) -> list[list[float]]:
        return [[0.1] * 768 for _ in texts]

    def get_token_count(self, text: str) -> int:
        return len(text.split())


def test_fact_candidate_schema():
    # Valid candidate
    fact = FactCandidate(
        fact_text="PostgreSQL veritabanı kullanılıyor.",
        subject="Proje",
        predicate="veritabanı",
        object="PostgreSQL",
        confidence=0.95,
        source_span="PostgreSQL veritabanı",
    )
    assert fact.fact_text == "PostgreSQL veritabanı kullanılıyor."
    assert fact.subject == "Proje"
    assert fact.predicate == "veritabanı"
    assert fact.object == "PostgreSQL"
    assert fact.confidence == 0.95

    # Stripping whitespace
    fact_ws = FactCandidate(
        fact_text="  PostgreSQL veritabanı  ",
        subject="  Proje  ",
        predicate="  veritabanı  ",
        object="  PostgreSQL  ",
    )
    assert fact_ws.fact_text == "PostgreSQL veritabanı"
    assert fact_ws.subject == "Proje"

    # Empty field rejected
    with pytest.raises(ValueError):
        FactCandidate(
            fact_text="",
            subject="Proje",
            predicate="veritabanı",
            object="PostgreSQL",
        )

    # Invalid confidence rejected
    with pytest.raises(ValueError):
        FactCandidate(
            fact_text="Test",
            subject="S",
            predicate="P",
            object="O",
            confidence=1.5,
        )


def test_deterministic_fact_validator():
    validator = DeterministicFactValidator()

    valid_c1 = FactCandidate(
        fact_text="PostgreSQL kullanıyoruz.",
        subject="Proje",
        predicate="veritabanı",
        object="PostgreSQL",
        confidence=0.9,
    )
    valid_c2 = FactCandidate(
        fact_text="Backend FastAPI ile yazıldı.",
        subject="Backend",
        predicate="framework",
        object="FastAPI",
        confidence=0.85,
    )
    duplicate_c1 = FactCandidate(
        fact_text="PostgreSQL kullanıyoruz.",
        subject="proje",
        predicate="veritabanı",
        object="postgresql",
        confidence=0.9,
    )

    candidates = [valid_c1, valid_c2, duplicate_c1]
    filtered = validator.deduplicate_and_canonicalize(candidates)

    # Duplicate should be filtered out deterministically
    assert len(filtered) == 2
    assert filtered[0].object == "PostgreSQL"
    assert filtered[1].object == "FastAPI"


@pytest.mark.asyncio
async def test_fact_extraction_single_call_zero_facts():
    adapter = MockExtractionAdapter(
        responses=[
            FactExtractionResponse(facts=[]),
        ]
    )
    service = FactExtractionService(llm=adapter)

    facts = await service.extract_facts("Tamam teşekkür ederim, iyi günler.")
    assert len(facts) == 0
    assert adapter.complete_count == 1


@pytest.mark.asyncio
async def test_fact_extraction_single_call_multiple_facts():
    adapter = MockExtractionAdapter(
        responses=[
            FactExtractionResponse(
                facts=[
                    FactCandidate(
                        fact_text="Backend FastAPI ile geliştiriliyor.",
                        subject="Backend",
                        predicate="framework",
                        object="FastAPI",
                        confidence=0.95,
                    ),
                    FactCandidate(
                        fact_text="Veritabanı olarak PostgreSQL kullanılıyor.",
                        subject="Proje",
                        predicate="veritabanı",
                        object="PostgreSQL",
                        confidence=0.98,
                    ),
                ]
            )
        ]
    )
    service = FactExtractionService(llm=adapter)

    facts = await service.extract_facts("Backend FastAPI, DB ise PostgreSQL kullanıyor.")
    assert len(facts) == 2
    assert adapter.complete_count == 1
    assert facts[0].subject == "Backend"
    assert facts[1].subject == "Proje"


@pytest.mark.asyncio
async def test_fact_extraction_correction_retry_on_invalid_schema():
    # First response is raw invalid text / non-schema, second is valid FactExtractionResponse
    adapter = MockExtractionAdapter(
        responses=[
            ValueError("Malformed JSON response from model"),
            FactExtractionResponse(
                facts=[
                    FactCandidate(
                        fact_text="Koyu tema tercih ediliyor.",
                        subject="Kullanıcı",
                        predicate="arayüz_teması",
                        object="koyu_tema",
                        confidence=0.9,
                    )
                ]
            ),
        ]
    )
    service = FactExtractionService(llm=adapter)

    facts = await service.extract_facts("Arayüzde koyu tema kullanmayı seviyorum.")
    assert len(facts) == 1
    assert facts[0].object == "koyu_tema"
    # Exactly 2 calls: 1 initial + 1 correction retry
    assert adapter.complete_count == 2


@pytest.mark.asyncio
async def test_fact_extraction_bounded_retry_failure():
    # Both initial and retry fail
    adapter = MockExtractionAdapter(
        responses=[
            ValueError("Malformed JSON 1"),
            ValueError("Malformed JSON 2"),
        ]
    )
    service = FactExtractionService(llm=adapter)

    with pytest.raises(FactExtractionError):
        await service.extract_facts("Test metni")

    # Exactly 2 calls: bounded retry reached, no 3rd call
    assert adapter.complete_count == 2


def test_fact_candidate_mapping_to_extracted_triplet():
    candidates = [
        FactCandidate(
            fact_text="Backend FastAPI ile geliştiriliyor.",
            subject="Backend",
            predicate="framework",
            object="FastAPI",
            confidence=0.95,
        ),
        FactCandidate(
            fact_text="Veritabanı PostgreSQL.",
            subject="Proje",
            predicate="veritabanı",
            object="PostgreSQL",
            confidence=0.98,
        ),
    ]

    triplet = fact_candidates_to_extracted_triplet(candidates, record_index=0)
    assert triplet is not None
    assert triplet.record_index == 0
    assert triplet.head == "Backend"
    assert triplet.relation == "framework"
    assert triplet.tail == "FastAPI"
    assert len(triplet.additional_triplets) == 1
    assert triplet.additional_triplets[0]["head"] == "Proje"
    assert triplet.additional_triplets[0]["relation"] == "veritabanı"
    assert triplet.additional_triplets[0]["tail"] == "PostgreSQL"

    # Empty candidates returns None (0 facts)
    assert fact_candidates_to_extracted_triplet([], record_index=0) is None


@pytest.mark.asyncio
async def test_rebel_not_instantiated_when_disabled():
    with patch("mesa_memory.extraction.rebel_pipeline.RebelExtractor") as mock_rebel:
        mock_rebel.side_effect = RuntimeError("REBEL must not be instantiated")

        adapter = MockExtractionAdapter(
            responses=[FactExtractionResponse(facts=[])]
        )
        service = FactExtractionService(llm=adapter, rebel_enabled=False)
        facts = await service.extract_facts("Normal metin")
        assert len(facts) == 0
        mock_rebel.assert_not_called()
