from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import config
from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.consolidation.policy import (
    DeterministicOnlyValidationPolicy,
    SingleLLMValidationPolicy,
)
from mesa_memory.consolidation.schemas import (
    BatchExtractionResponse,
    ExtractedTriplet,
)
from mesa_workers.ingestion_worker import process_cold_path


class StateMachineTrackingAdapter(BaseUniversalLLMAdapter):
    def __init__(
        self,
        response: str = '{"decision": "STORE", "justification": "Approved"}',
        model_name: str = "tracker",
    ):
        self.response = response
        self.model_name = model_name
        self.complete_count = 0

    def complete(self, prompt: str, schema=None, **kwargs):
        self.complete_count += 1
        if schema is BatchExtractionResponse or (
            isinstance(schema, type) and issubclass(schema, BatchExtractionResponse)
        ):
            return BatchExtractionResponse(
                triplets=[
                    ExtractedTriplet(
                        record_index=0,
                        head="EntityX",
                        relation="connected_to",
                        tail="EntityY",
                    )
                ]
            )
        return self.response

    async def acomplete(self, prompt: str, schema=None, **kwargs):
        self.complete_count += 1
        if schema is BatchExtractionResponse or (
            isinstance(schema, type) and issubclass(schema, BatchExtractionResponse)
        ):
            return BatchExtractionResponse(
                triplets=[
                    ExtractedTriplet(
                        record_index=0,
                        head="EntityX",
                        relation="connected_to",
                        tail="EntityY",
                    )
                ]
            )
        return self.response

    def embed(self, text: str, **kwargs) -> list[float]:
        return [0.1] * config.embedding_dimension

    async def aembed(self, text: str, **kwargs) -> list[float]:
        return [0.1] * config.embedding_dimension

    def embed_batch(self, texts: list[str], **kwargs) -> list[list[float]]:
        return [[0.1] * config.embedding_dimension for _ in texts]

    async def aembed_batch(self, texts: list[str], **kwargs) -> list[list[float]]:
        return [[0.1] * config.embedding_dimension for _ in texts]

    def get_token_count(self, text: str) -> int:
        return len(text.split())


@pytest.mark.asyncio
async def test_mode_0_state_machine_transitions(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "rebel_enabled", False)

    dao = MagicMock()
    dao.get_agent_embeddings.return_value = []
    dao.claim_raw_log = AsyncMock(return_value={"id": 1})
    dao.get_raw_log = AsyncMock(
        return_value={
            "id": 1,
            "status": "DEFERRED",
            "payload": {
                "tenant_id": "tenant_1",
                "workspace_id": "ws_1",
                "dataset_id": "ds_1",
                "document_id": "doc_1",
                "agent_id": "agent_1",
                "session_id": "session_1",
                "content": "Mode 0 verified text",
                "validation_mode": 0,
            },
        }
    )
    dao.update_raw_log_status = AsyncMock()
    dao.transition_raw_log_status = AsyncMock()
    dao.record_mutation = AsyncMock()
    dao.record_mutation_tier3_audit = AsyncMock()
    dao.set_mutation_state = AsyncMock()
    dao.insert_memory = AsyncMock(return_value="node_1")
    dao.insert_edge = AsyncMock(return_value="edge_1")

    embedder = StateMachineTrackingAdapter(model_name="embedder")
    mode_0_policy = DeterministicOnlyValidationPolicy()

    loop = ConsolidationLoop(
        dao=dao,
        embedder=embedder,
        validation_policy=mode_0_policy,
        extraction_llm=embedder,
        queue_root=tmp_path / "queue_sm_0",
    )

    await process_cold_path(
        log_id=1,
        agent_id="agent_1",
        dao=dao,
        consolidation_loop=loop,
        model_processing_enabled=True,
    )

    # State Machine checks:
    # 1. Mutation state set to VALIDATED
    dao.set_mutation_state.assert_awaited_once()
    args, kwargs = dao.set_mutation_state.call_args
    assert args[2] == "VALIDATED"
    assert (
        kwargs.get("event_detail", {})
        .get("tier3", {})
        .get("decisions", {})
        .get("primary")
        == "SKIPPED_BY_POLICY"
    )
    assert "failure_class" not in kwargs or kwargs.get("failure_class") is None

    # 2. Raw log transitioned to processed
    dao.update_raw_log_status.assert_awaited()
    last_status = dao.update_raw_log_status.call_args[0][2]
    assert last_status == "processed"


@pytest.mark.asyncio
async def test_mode_1_cognitive_discard_transitions(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "rebel_enabled", False)

    dao = MagicMock()
    dao.get_agent_embeddings.return_value = []
    dao.claim_raw_log = AsyncMock(return_value={"id": 2})
    dao.get_raw_log = AsyncMock(
        return_value={
            "id": 2,
            "status": "DEFERRED",
            "payload": {
                "tenant_id": "tenant_1",
                "workspace_id": "ws_1",
                "dataset_id": "ds_1",
                "document_id": "doc_2",
                "agent_id": "agent_1",
                "session_id": "session_1",
                "content": "Spam message discarded by cognitive filter",
                "validation_mode": 1,
            },
        }
    )
    dao.update_raw_log_status = AsyncMock()
    dao.transition_raw_log_status = AsyncMock()
    dao.record_mutation = AsyncMock()
    dao.record_mutation_tier3_audit = AsyncMock()
    dao.set_mutation_state = AsyncMock()
    dao.insert_memory = AsyncMock(return_value="node_1")
    dao.insert_edge = AsyncMock(return_value="edge_1")

    validator_discard = StateMachineTrackingAdapter(
        response='{"decision": "DISCARD", "justification": "Irrelevant noise"}',
        model_name="val_discard",
    )
    embedder = StateMachineTrackingAdapter(model_name="embedder")
    mode_1_policy = SingleLLMValidationPolicy(validator_discard)

    loop = ConsolidationLoop(
        dao=dao,
        embedder=embedder,
        validation_policy=mode_1_policy,
        extraction_llm=embedder,
        queue_root=tmp_path / "queue_sm_1",
    )

    await process_cold_path(
        log_id=2,
        agent_id="agent_1",
        dao=dao,
        consolidation_loop=loop,
        model_processing_enabled=True,
    )

    # State Machine checks:
    # 1. Mutation state set to REJECTED with Tier3Rejected failure class
    dao.set_mutation_state.assert_awaited_once()
    args, kwargs = dao.set_mutation_state.call_args
    assert args[2] == "REJECTED"
    assert kwargs.get("failure_class") == "Tier3Rejected"

    # 2. Raw log transitioned to rejected
    dao.update_raw_log_status.assert_awaited()
    last_status = dao.update_raw_log_status.call_args[0][2]
    assert last_status == "rejected"
