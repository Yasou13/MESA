import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import config, configured_embedding_identity
from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.consolidation.policy import (
    DeterministicOnlyValidationPolicy,
    SingleLLMValidationPolicy,
)
from mesa_memory.consolidation.schemas import (
    BatchExtractionResponse,
    ExtractedTriplet,
    MemoryCandidate,
)
from mesa_memory.extraction.service import FactExtractionResponse
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


class TrackingAdapter(BaseUniversalLLMAdapter):
    def __init__(
        self,
        name: str,
        response: str = '{"decision": "STORE", "justification": "Valid"}',
    ):
        self.name = name
        self.response = response
        self.complete_count = 0
        self.embed_count = 0

    def complete(self, prompt: str, schema=None, **kwargs):
        self.complete_count += 1
        if schema is FactExtractionResponse:
            return {
                "facts": [
                    {
                        "fact_text": "EntityA is connected to EntityB.",
                        "subject": "EntityA",
                        "predicate": "connected_to",
                        "object": "EntityB",
                        "confidence": 0.9,
                        "source_span": "EntityA",
                    }
                ]
            }
        if schema is BatchExtractionResponse or (
            isinstance(schema, type) and issubclass(schema, BatchExtractionResponse)
        ):
            return BatchExtractionResponse(
                triplets=[
                    ExtractedTriplet(
                        record_index=0,
                        head="EntityA",
                        relation="connected_to",
                        tail="EntityB",
                    )
                ]
            )
        return self.response

    async def acomplete(self, prompt: str, schema=None, **kwargs):
        return self.complete(prompt, schema, **kwargs)

    def embed(self, text: str, **kwargs) -> list[float]:
        self.embed_count += 1
        return [0.42] * config.embedding_dimension

    async def aembed(self, text: str, **kwargs) -> list[float]:
        self.embed_count += 1
        return [0.42] * config.embedding_dimension

    def embed_batch(self, texts: list[str], **kwargs) -> list[list[float]]:
        self.embed_count += len(texts)
        return [[0.42] * config.embedding_dimension for _ in texts]

    async def aembed_batch(self, texts: list[str], **kwargs) -> list[list[float]]:
        self.embed_count += len(texts)
        return [[0.42] * config.embedding_dimension for _ in texts]

    def get_token_count(self, text: str) -> int:
        return len(text.split())


def make_mock_dao():
    dao = MagicMock()
    dao.get_agent_embeddings.return_value = []
    dao.insert_memory = AsyncMock(return_value="node_1")
    dao.insert_edge = AsyncMock(return_value="edge_1")
    dao.get_memories = AsyncMock(return_value=[])
    dao.invalidate_node = AsyncMock()
    dao.record_mutation = AsyncMock()
    dao.record_mutation_extraction = AsyncMock()
    dao.record_mutation_tier3_audit = AsyncMock()
    dao.set_mutation_state = AsyncMock()
    return dao


@pytest.mark.asyncio
async def test_mode_0_extraction_runs_with_zero_validation_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "rebel_enabled", False)

    extraction_adapter = TrackingAdapter("extraction_llm")
    embedder = TrackingAdapter("embedder")
    mode_0_policy = DeterministicOnlyValidationPolicy()

    dao = make_mock_dao()
    loop = ConsolidationLoop(
        dao=dao,
        embedder=embedder,
        validation_policy=mode_0_policy,
        extraction_llm=extraction_adapter,
        queue_root=tmp_path / "queue_mode0_ext",
    )

    candidate = MemoryCandidate.from_raw_log(
        raw_log_id=301,
        agent_id="test_agent",
        session_id="session_1",
        content_payload="EntityA is connected to EntityB in network architecture.",
        metadata={"_mesa_validation_mode": 0},
        embedding_provider="openai_compatible",
        embedding_model="text-embedding-3-small",
        embedding_version="v1",
        embedding_dimension=384,
    )
    record = candidate.as_consolidation_record()

    outcome = await loop.run_batch([record])

    assert candidate.candidate_id in outcome["accepted"]
    # Extraction LLM ran
    assert extraction_adapter.complete_count >= 1
    # Validation policy was DeterministicOnly (0 validation LLMs)
    assert mode_0_policy.validator_count == 0
    assert record["_mesa_tier3_audit"]["route"] == "deterministic_only"


@pytest.mark.asyncio
async def test_mode_1_extraction_and_validation_independent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "rebel_enabled", False)

    extraction_adapter = TrackingAdapter("extraction_llm")
    validator_a = TrackingAdapter(
        "validator_a",
        response='{"decision": "STORE", "justification": "Approved by A"}',
    )
    embedder = TrackingAdapter("embedder")
    mode_1_policy = SingleLLMValidationPolicy(validator_a)

    dao = make_mock_dao()
    loop = ConsolidationLoop(
        dao=dao,
        embedder=embedder,
        validation_policy=mode_1_policy,
        extraction_llm=extraction_adapter,
        queue_root=tmp_path / "queue_mode1_ext",
    )

    candidate = MemoryCandidate.from_raw_log(
        raw_log_id=302,
        agent_id="test_agent",
        session_id="session_1",
        content_payload="Database shard 2 migrated to host B.",
        metadata={"_mesa_validation_mode": 1},
        embedding_provider="openai_compatible",
        embedding_model="text-embedding-3-small",
        embedding_version="v1",
        embedding_dimension=384,
    )
    record = candidate.as_consolidation_record()

    outcome = await loop.run_batch([record])

    assert candidate.candidate_id in outcome["accepted"]
    # Validator A was called exactly once for validation
    assert validator_a.complete_count == 1
    # Extraction adapter was called for extraction
    assert extraction_adapter.complete_count >= 1


@pytest.mark.asyncio
async def test_injected_policy_never_becomes_an_extraction_fallback(
    tmp_path, monkeypatch
):
    """A runtime-composed validator must not silently serve extraction."""
    monkeypatch.setattr(config, "rebel_enabled", False)

    validator_a = TrackingAdapter("validator_a")
    embedder = TrackingAdapter("embedder")
    loop = ConsolidationLoop(
        dao=make_mock_dao(),
        embedder=embedder,
        validation_policy=SingleLLMValidationPolicy(validator_a),
        queue_root=tmp_path / "queue_policy_extraction_boundary",
    )
    candidate = MemoryCandidate.from_raw_log(
        raw_log_id=303,
        agent_id="test_agent",
        session_id="session_1",
        content_payload="Service A publishes an event to Service B.",
        metadata={"_mesa_validation_mode": 1},
        embedding_provider="openai_compatible",
        embedding_model="text-embedding-3-small",
        embedding_version="v1",
        embedding_dimension=384,
    )

    outcome = await loop.run_batch([candidate.as_consolidation_record()])

    assert candidate.candidate_id in outcome["accepted"]
    assert validator_a.complete_count == 1
    assert embedder.complete_count >= 1


from mesa_memory.consolidation.policy import (
    DeterministicOnlyValidationPolicy,
    DualLLMValidationPolicy,
    SingleLLMValidationPolicy,
)
from mesa_memory.consolidation.validator import Tier3Validator


@pytest.mark.asyncio
async def test_mode_2_validation_does_not_duplicate_extraction(tmp_path, monkeypatch):
    """Mode 2 uses 2 validators but extraction call count MUST remain exactly 1."""
    monkeypatch.setattr(config, "rebel_enabled", False)

    extraction_adapter = TrackingAdapter("extraction_llm")
    validator_a = TrackingAdapter(
        "validator_a",
        response='{"decision": "STORE", "justification": "Approved by A"}',
    )
    validator_b = TrackingAdapter(
        "validator_b",
        response='{"decision": "STORE", "justification": "Approved by B"}',
    )
    embedder = TrackingAdapter("embedder")
    tier3_validator = Tier3Validator(validator_a, validator_b)
    mode_2_policy = DualLLMValidationPolicy(tier3_validator)

    dao = make_mock_dao()
    loop = ConsolidationLoop(
        dao=dao,
        embedder=embedder,
        validation_policy=mode_2_policy,
        extraction_llm=extraction_adapter,
        queue_root=tmp_path / "queue_mode2_ext",
    )

    candidate = MemoryCandidate.from_raw_log(
        raw_log_id=304,
        agent_id="test_agent",
        session_id="session_1",
        content_payload="Distributed consensus protocol validated across cluster nodes.",
        metadata={"_mesa_validation_mode": 2},
        embedding_provider="openai_compatible",
        embedding_model="text-embedding-3-small",
        embedding_version="v1",
        embedding_dimension=384,
    )
    record = candidate.as_consolidation_record()

    outcome = await loop.run_batch([record])

    assert candidate.candidate_id in outcome["accepted"]
    # Both validators participated in validation
    assert validator_a.complete_count == 1
    assert validator_b.complete_count == 1
    # Extraction was called EXACTLY ONCE (not duplicated for Mode 2)
    assert extraction_adapter.complete_count == 1


@pytest.mark.asyncio
async def test_rebel_disabled_guarantees_zero_rebel_instantiation(tmp_path, monkeypatch):
    """With MESA_REBEL_ENABLED=false, RebelExtractor is not instantiated."""
    monkeypatch.setattr(config, "rebel_enabled", False)

    with patch("mesa_memory.extraction.rebel_pipeline.RebelExtractor") as mock_rebel:
        mock_rebel.side_effect = RuntimeError("REBEL must not be instantiated")

        extraction_adapter = TrackingAdapter("extraction_llm")
        embedder = TrackingAdapter("embedder")
        loop = ConsolidationLoop(
            dao=make_mock_dao(),
            embedder=embedder,
            validation_policy=DeterministicOnlyValidationPolicy(),
            extraction_llm=extraction_adapter,
            queue_root=tmp_path / "queue_rebel_disabled",
        )

        candidate = MemoryCandidate.from_raw_log(
            raw_log_id=305,
            agent_id="test_agent",
            session_id="session_1",
            content_payload="Kullanıcı PostgreSQL tercih ediyor.",
            metadata={"_mesa_validation_mode": 0},
            embedding_provider="openai_compatible",
            embedding_model="text-embedding-3-small",
            embedding_version="v1",
            embedding_dimension=384,
        )

        outcome = await loop.run_batch([candidate.as_consolidation_record()])

        assert candidate.candidate_id in outcome["accepted"]
        assert extraction_adapter.complete_count == 1
        mock_rebel.assert_not_called()


def test_embedding_identity_independence():
    # Embedding identity is configured independently of Tier-3 validation mode
    identity = configured_embedding_identity()
    assert identity.provider is not None
    assert identity.model is not None
    assert identity.version is not None
    assert identity.dimension == 768


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [0, 1, 2])
async def test_canonical_v4_uses_single_fact_extractor_across_validation_modes(
    tmp_path, mode
):
    """The real mutation lane must never fall back to TripletExtractor."""
    engine = AsyncEngine(str(tmp_path / f"canonical-mode-{mode}.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    await dao.initialize()
    extraction = TrackingAdapter("extraction")
    validator_a = TrackingAdapter("validator_a")
    validator_b = TrackingAdapter("validator_b")
    if mode == 0:
        policy = DeterministicOnlyValidationPolicy()
    elif mode == 1:
        policy = SingleLLMValidationPolicy(validator_a)
    else:
        policy = DualLLMValidationPolicy(Tier3Validator(validator_a, validator_b))
    identity = configured_embedding_identity()
    candidate = MemoryCandidate.from_raw_log(
        raw_log_id=900 + mode,
        agent_id="tenant-a",
        session_id="session-a",
        content_payload="EntityA is connected to EntityB.",
        metadata={"_mesa_validation_mode": mode},
        embedding_provider=identity.provider,
        embedding_model=identity.model,
        embedding_version=identity.version,
        embedding_dimension=identity.dimension,
        validation_mode=mode,
    )
    record = candidate.as_consolidation_record()
    try:
        await dao.record_mutation(record, raw_log_id=candidate.raw_log_id)
        loop = ConsolidationLoop(
            dao=dao,
            embedder=TrackingAdapter("legacy_embedder"),
            validation_policy=policy,
            extraction_llm=extraction,
            queue_root=tmp_path / f"queue-{mode}",
        )
        loop.triplet_extractor.extract_batch = AsyncMock(
            side_effect=AssertionError("canonical V4 must not use TripletExtractor")
        )

        outcome = await loop.run_batch([record])

        assert outcome["accepted"] == [candidate.candidate_id]
        assert extraction.complete_count == 1
        assert validator_a.complete_count == (1 if mode in (1, 2) else 0)
        assert validator_b.complete_count == (1 if mode == 2 else 0)
        mutation = await dao.get_mutation("tenant-a", candidate.mutation_id)
        assert mutation is not None
        metadata = json.loads(mutation["metadata_json"])
        assert metadata["_mesa_v4_projection_triplets"] == [
            {
                "confidence": 0.9,
                "fact_text": "EntityA is connected to EntityB.",
                "head": "EntityA",
                "literal_value": None,
                "metadata": {},
                "relation": "connected_to",
                "source_span": "EntityA",
                "supersedes": None,
                "tail": "EntityB",
                "valid_from": None,
                "valid_to": None,
            }
        ]
    finally:
        await engine.close()
