import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import MesaConfig, config
from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.consolidation.policy import (
    DeterministicOnlyValidationPolicy,
    SingleLLMValidationPolicy,
    DualLLMValidationPolicy,
    get_validation_policy,
)
from mesa_memory.consolidation.schemas import BatchExtractionResponse, ExtractedTriplet, MemoryCandidate
from mesa_memory.consolidation.validator import Tier3Validator
from mesa_storage.dao import MemoryDAO


class E2ETrackingAdapter(BaseUniversalLLMAdapter):
    def __init__(self, name: str, response: str = '{"decision": "STORE", "justification": "E2E Approved"}'):
        self.name = name
        self.response = response
        self.call_count = 0

    def complete(self, prompt: str, schema=None, **kwargs):
        self.call_count += 1
        if schema is BatchExtractionResponse or (isinstance(schema, type) and issubclass(schema, BatchExtractionResponse)):
            return BatchExtractionResponse(
                triplets=[
                    ExtractedTriplet(
                        record_index=0,
                        head="Microservice",
                        relation="calls",
                        tail="AuthDatabase",
                    )
                ]
            )
        return self.response

    async def acomplete(self, prompt: str, schema=None, **kwargs):
        self.call_count += 1
        if schema is BatchExtractionResponse or (isinstance(schema, type) and issubclass(schema, BatchExtractionResponse)):
            return BatchExtractionResponse(
                triplets=[
                    ExtractedTriplet(
                        record_index=0,
                        head="Microservice",
                        relation="calls",
                        tail="AuthDatabase",
                    )
                ]
            )
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


from types import SimpleNamespace
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.mark.asyncio
async def test_e2e_mode_0_deterministic_matrix(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "rebel_enabled", False)
    db_path = str(tmp_path / "test_e2e_0.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())

    await dao.ensure_v4_catalog_scope(
        tenant_id="tenant_main", workspace_id="ws_main", dataset_id="ds_main"
    )
    session = await dao.create_v4_session(
        tenant_id="tenant_main",
        workspace_id="ws_main",
        dataset_ids=["ds_main"],
        agent_id="agent_sys",
        principal_id="sys-principal",
    )

    embedder = E2ETrackingAdapter("embedder")
    extractor = E2ETrackingAdapter("extractor")
    policy_0 = get_validation_policy(0)

    assert policy_0.mode == 0
    assert policy_0.validator_count == 0
    assert policy_0.llm_validation_enabled is False

    loop = ConsolidationLoop(
        dao=dao,
        embedder=embedder,
        validation_policy=policy_0,
        extraction_llm=extractor,
        queue_root=tmp_path / "queue_e2e_0",
    )

    # 1. Admit memory under Mode 0
    admitted = await dao.admit_v4_memory(
        tenant_id="tenant_main",
        workspace_id="ws_main",
        dataset_id="ds_main",
        agent_id="agent_sys",
        session_id=session["session_id"],
        document_id="doc_100",
        revision_id="rev_100",
        chunk_id="chunk_100",
        title="Microservice Architecture",
        content_payload="Microservice calls AuthDatabase on port 5432.",
        source_ref="test_ref",
        evidence_span="0:45",
        revision_number=1,
        chunk_ordinal=0,
        supersedes_revision_id=None,
        metadata={"_mesa_validation_mode": 0},
        embedding_provider="openai_compatible",
        embedding_model="text-embedding-3-small",
        embedding_version="v1",
        embedding_dimension=384,
        policy=config.queue_admission_policy,
    )
    raw_log_id = admitted["response"]["raw_log_id"]
    mutation_id = admitted["response"]["mutation_id"]

    candidate = MemoryCandidate.from_raw_log(
        raw_log_id=raw_log_id,
        agent_id="agent_sys",
        session_id=session["session_id"],
        content_payload="Microservice calls AuthDatabase on port 5432.",
        metadata={"_mesa_validation_mode": 0, "mutation_id": mutation_id},
        embedding_provider="openai_compatible",
        embedding_model="text-embedding-3-small",
        embedding_version="v1",
        embedding_dimension=384,
    )
    record = candidate.as_consolidation_record()
    record["mutation_id"] = mutation_id

    # 2. Run consolidation loop
    outcome = await loop.run_batch([record])
    assert candidate.candidate_id in outcome["accepted"]

    # 3. Assert zero validator calls and SKIPPED_BY_POLICY receipt
    audit = record.get("_mesa_tier3_audit")
    assert audit is not None
    assert audit["route"] == "deterministic_only"
    assert audit["reason"] == "skipped_by_policy"
    assert audit["decisions"]["primary"] == "SKIPPED_BY_POLICY"

    await engine.close()


@pytest.mark.asyncio
async def test_e2e_mode_1_single_model_matrix(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "rebel_enabled", False)
    db_path = str(tmp_path / "test_e2e_1.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())

    await dao.ensure_v4_catalog_scope(
        tenant_id="tenant_main", workspace_id="ws_main", dataset_id="ds_main"
    )
    session = await dao.create_v4_session(
        tenant_id="tenant_main",
        workspace_id="ws_main",
        dataset_ids=["ds_main"],
        agent_id="agent_sys",
        principal_id="sys-principal",
    )

    val_a = E2ETrackingAdapter("val_a", response='{"decision": "STORE", "justification": "Valid microservice architecture"}')
    embedder = E2ETrackingAdapter("embedder")
    extractor = E2ETrackingAdapter("extractor")
    policy_1 = get_validation_policy(1, val_a)

    assert policy_1.mode == 1
    assert policy_1.validator_count == 1
    assert policy_1.llm_validation_enabled is True

    loop = ConsolidationLoop(
        dao=dao,
        embedder=embedder,
        validation_policy=policy_1,
        extraction_llm=extractor,
        queue_root=tmp_path / "queue_e2e_1",
    )

    admitted = await dao.admit_v4_memory(
        tenant_id="tenant_main",
        workspace_id="ws_main",
        dataset_id="ds_main",
        agent_id="agent_sys",
        session_id=session["session_id"],
        document_id="doc_200",
        revision_id="rev_200",
        chunk_id="chunk_200",
        title="Microservice Architecture",
        content_payload="Microservice calls AuthDatabase on port 5432.",
        source_ref="test_ref",
        evidence_span="0:45",
        revision_number=1,
        chunk_ordinal=0,
        supersedes_revision_id=None,
        metadata={"_mesa_validation_mode": 1},
        embedding_provider="openai_compatible",
        embedding_model="text-embedding-3-small",
        embedding_version="v1",
        embedding_dimension=384,
        policy=config.queue_admission_policy,
    )
    raw_log_id = admitted["response"]["raw_log_id"]
    mutation_id = admitted["response"]["mutation_id"]

    candidate = MemoryCandidate.from_raw_log(
        raw_log_id=raw_log_id,
        agent_id="agent_sys",
        session_id=session["session_id"],
        content_payload="Microservice calls AuthDatabase on port 5432.",
        metadata={"_mesa_validation_mode": 1, "mutation_id": mutation_id},
        embedding_provider="openai_compatible",
        embedding_model="text-embedding-3-small",
        embedding_version="v1",
        embedding_dimension=384,
    )
    record = candidate.as_consolidation_record()
    record["mutation_id"] = mutation_id

    outcome = await loop.run_batch([record])
    assert candidate.candidate_id in outcome["accepted"]
    assert val_a.call_count == 1

    audit = record.get("_mesa_tier3_audit")
    assert audit is not None
    assert audit["route"] == "single_model"
    assert audit["decisions"]["primary"] == "STORE"

    await engine.close()


@pytest.mark.asyncio
async def test_e2e_mode_2_dual_consensus_matrix(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "rebel_enabled", False)
    db_path = str(tmp_path / "test_e2e_2.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())

    await dao.ensure_v4_catalog_scope(
        tenant_id="tenant_main", workspace_id="ws_main", dataset_id="ds_main"
    )
    session = await dao.create_v4_session(
        tenant_id="tenant_main",
        workspace_id="ws_main",
        dataset_ids=["ds_main"],
        agent_id="agent_sys",
        principal_id="sys-principal",
    )

    val_a = E2ETrackingAdapter("val_a", response='{"decision": "STORE", "justification": "A agrees"}')
    val_b = E2ETrackingAdapter("val_b", response='{"decision": "DISCARD", "justification": "B rejects"}')
    embedder = E2ETrackingAdapter("embedder")
    extractor = E2ETrackingAdapter("extractor")
    policy_2 = get_validation_policy(2, val_a, val_b)

    assert policy_2.mode == 2
    assert policy_2.validator_count == 2
    assert policy_2.llm_validation_enabled is True

    loop = ConsolidationLoop(
        dao=dao,
        embedder=embedder,
        validation_policy=policy_2,
        extraction_llm=extractor,
        queue_root=tmp_path / "queue_e2e_2",
    )

    admitted = await dao.admit_v4_memory(
        tenant_id="tenant_main",
        workspace_id="ws_main",
        dataset_id="ds_main",
        agent_id="agent_sys",
        session_id=session["session_id"],
        document_id="doc_300",
        revision_id="rev_300",
        chunk_id="chunk_300",
        title="Controversial Spec",
        content_payload="Controversial fact with disagreement.",
        source_ref="test_ref",
        evidence_span="0:38",
        revision_number=1,
        chunk_ordinal=0,
        supersedes_revision_id=None,
        metadata={"_mesa_validation_mode": 2},
        embedding_provider="openai_compatible",
        embedding_model="text-embedding-3-small",
        embedding_version="v1",
        embedding_dimension=384,
        policy=config.queue_admission_policy,
    )
    raw_log_id = admitted["response"]["raw_log_id"]
    mutation_id = admitted["response"]["mutation_id"]

    candidate = MemoryCandidate.from_raw_log(
        raw_log_id=raw_log_id,
        agent_id="agent_sys",
        session_id=session["session_id"],
        content_payload="Controversial fact with disagreement.",
        metadata={"_mesa_validation_mode": 2, "mutation_id": mutation_id},
        embedding_provider="openai_compatible",
        embedding_model="text-embedding-3-small",
        embedding_version="v1",
        embedding_dimension=384,
    )
    record = candidate.as_consolidation_record()
    record["mutation_id"] = mutation_id

    # Disagreement between A and B must reject (fail closed)
    outcome = await loop.run_batch([record])
    assert candidate.candidate_id in outcome["rejected"]
    assert val_a.call_count == 1
    assert val_b.call_count == 1

    await engine.close()
