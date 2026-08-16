import pytest
from unittest.mock import MagicMock

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import MesaConfig
from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.consolidation.policy import (
    DeterministicOnlyValidationPolicy,
    DualLLMValidationPolicy,
    SingleLLMValidationPolicy,
)
from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_memory.consolidation.validator import Tier3Validator, Tier3ValidationError


class MockAdapter(BaseUniversalLLMAdapter):
    def __init__(self, response: str = '{"decision": "STORE", "justification": "Approved"}', model_name: str = "mock-model"):
        self.response = response
        self.model_name = model_name
        self.call_count = 0

    def complete(self, prompt: str, schema=None, **kwargs):
        self.call_count += 1
        if schema is not None:
            try:
                return schema(triplets=[])
            except Exception:
                pass
        return self.response

    async def acomplete(self, prompt: str, schema=None, **kwargs):
        self.call_count += 1
        if schema is not None:
            try:
                return schema(triplets=[])
            except Exception:
                pass
        return self.response

    def embed(self, text: str, **kwargs) -> list[float]:
        return [0.1] * 384

    async def aembed(self, text: str, **kwargs) -> list[float]:
        return [0.1] * 384

    def embed_batch(self, texts: list[str], **kwargs) -> list[list[float]]:
        return [[0.1] * 384 for _ in texts]

    async def aembed_batch(self, texts: list[str], **kwargs) -> list[list[float]]:
        return [[0.1] * 384 for _ in texts]

    def get_token_count(self, text: str) -> int:
        return len(text.split())


@pytest.mark.asyncio
async def test_durable_snapshot_mode_2_preserved_on_mode_0_server(tmp_path):
    # Candidate admitted under Mode 2
    candidate_mode_2 = MemoryCandidate.from_raw_log(
        raw_log_id=101,
        agent_id="agent_alpha",
        session_id="session_1",
        content_payload="Critical financial record",
        metadata={"_mesa_validation_mode": 2},
        embedding_provider="openai_compatible",
        embedding_model="text-embedding-3-small",
        embedding_version="v1",
        embedding_dimension=384,
    )
    assert candidate_mode_2.validation_mode == 2
    record_mode_2 = candidate_mode_2.as_consolidation_record()
    assert record_mode_2["validation_mode"] == 2

    # Server loop is initialized with Mode 0 (Deterministic Only)
    dao = MagicMock()
    embedder = MockAdapter()
    mode_0_policy = DeterministicOnlyValidationPolicy()

    loop_mode_0 = ConsolidationLoop(
        dao=dao,
        embedder=embedder,
        validation_policy=mode_0_policy,
        queue_root=tmp_path / "queue_1",
    )

    # Processing the Mode 2 record on Mode 0 server:
    # Mode 2 requires validators A and B. Since server is Mode 0 without configured Mode 2 adapters,
    # it must NOT silently bypass validation! It should raise Tier3ValidationError / defer.
    outcome = await loop_mode_0.run_batch([record_mode_2])
    assert candidate_mode_2.candidate_id in outcome["deferred"]
    assert candidate_mode_2.candidate_id not in outcome["accepted"]


@pytest.mark.asyncio
async def test_durable_snapshot_mode_0_preserved_on_mode_2_server(tmp_path):
    # Candidate admitted under Mode 0
    candidate_mode_0 = MemoryCandidate.from_raw_log(
        raw_log_id=202,
        agent_id="agent_beta",
        session_id="session_2",
        content_payload="Trusted local ingest",
        metadata={"_mesa_validation_mode": 0},
        embedding_provider="openai_compatible",
        embedding_model="text-embedding-3-small",
        embedding_version="v1",
        embedding_dimension=384,
    )
    assert candidate_mode_0.validation_mode == 0
    record_mode_0 = candidate_mode_0.as_consolidation_record()
    assert record_mode_0["validation_mode"] == 0

    # Server loop is initialized with Mode 2 (Dual LLM Consensus)
    dao = MagicMock()
    embedder = MockAdapter()
    adapter_a = MockAdapter(model_name="val-a")
    adapter_b = MockAdapter(model_name="val-b")
    mode_2_policy = DualLLMValidationPolicy(Tier3Validator(adapter_a, adapter_b))

    extractor = MockAdapter(model_name="extractor")
    loop_mode_2 = ConsolidationLoop(
        dao=dao,
        embedder=embedder,
        validation_policy=mode_2_policy,
        extraction_llm=extractor,
        queue_root=tmp_path / "queue_2",
    )

    # Processing the Mode 0 record on Mode 2 server:
    # Mode 0 snapshot means ZERO validation LLM calls and immediate acceptance.
    outcome = await loop_mode_2.run_batch([record_mode_0])
    assert candidate_mode_0.candidate_id in outcome["accepted"]
    assert adapter_a.call_count == 0
    assert adapter_b.call_count == 0
    assert record_mode_0["_mesa_tier3_audit"]["reason"] == "skipped_by_policy"
    assert record_mode_0["_mesa_tier3_audit"]["decisions"]["primary"] == "SKIPPED_BY_POLICY"
