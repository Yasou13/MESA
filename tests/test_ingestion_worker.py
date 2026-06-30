"""
MESA v0.4.0 — Ingestion Worker Test Suite
Unit tests for mesa_workers/ingestion_worker.py
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mesa_workers.ingestion_worker import (
    _commit_raw_memory,
    _commit_triplets,
    _parse_llm_triplet_response,
    process_cold_path,
)

# ===================================================================
# _parse_llm_triplet_response
# ===================================================================


class TestParseLLMTripletResponse:
    def test_valid_json_legacy_format(self):
        raw = '[{"head": "Company A", "relation": "acquired", "tail": "Company B"}]'
        triplets = _parse_llm_triplet_response(raw)
        assert len(triplets) == 1
        assert triplets[0]["head"] == "Company A"
        assert triplets[0]["relation"] == "acquired"
        assert triplets[0]["tail"] == "Company B"
        assert triplets[0]["confidence"] == "1.0"  # Default

    def test_valid_json_new_format(self):
        raw = '[{"subject": "Person X", "predicate": "knows", "object": "Person Y", "confidence": 0.85}]'
        triplets = _parse_llm_triplet_response(raw)
        assert len(triplets) == 1
        assert triplets[0]["head"] == "Person X"
        assert triplets[0]["relation"] == "knows"
        assert triplets[0]["tail"] == "Person Y"
        assert triplets[0]["confidence"] == "0.85"

    def test_confidence_clamping(self):
        # Should clamp to 1.0 if > 1.0, and 0.0 if < 0.0
        raw = '[{"head": "A", "relation": "R", "tail": "B", "confidence": 1.5}, {"head": "C", "relation": "R", "tail": "D", "confidence": -0.5}]'
        triplets = _parse_llm_triplet_response(raw)
        assert triplets[0]["confidence"] == "1.0"
        assert triplets[1]["confidence"] == "0.0"

    def test_confidence_fallback_on_invalid_type(self):
        raw = '[{"head": "A", "relation": "R", "tail": "B", "confidence": "high"}]'
        triplets = _parse_llm_triplet_response(raw)
        assert triplets[0]["confidence"] == "1.0"

    def test_markdown_fence_sanitisation(self):
        raw = '```json\n[{"head": "A", "relation": "R", "tail": "B"}]\n```'
        triplets = _parse_llm_triplet_response(raw)
        assert len(triplets) == 1

    def test_trailing_comma_repair(self):
        raw = '[{"head": "A", "relation": "R", "tail": "B"},]'
        triplets = _parse_llm_triplet_response(raw)
        assert len(triplets) == 1

    def test_invalid_json_returns_empty(self):
        raw = "Not JSON at all"
        triplets = _parse_llm_triplet_response(raw)
        assert len(triplets) == 0

    def test_missing_required_fields(self):
        # Missing 'relation'
        raw = '[{"head": "A", "tail": "B"}]'
        triplets = _parse_llm_triplet_response(raw)
        assert len(triplets) == 0


# ===================================================================
# Graph Commit logic (DAO and Adapter Mocking)
# ===================================================================


class TestCommitLogic:
    @pytest.mark.asyncio
    async def test_commit_triplets(self):
        mock_dao = MagicMock()
        mock_dao.vector_engine.compute_embedding = AsyncMock(return_value=[0.1] * 768)
        mock_dao.insert_memory = AsyncMock(side_effect=["node_head_id", "node_tail_id"])
        mock_dao.insert_edge = AsyncMock()

        triplets = [
            {
                "head": "EntityA",
                "relation": "related_to",
                "tail": "EntityB",
                "confidence": "0.8",
            }
        ]

        await _commit_triplets(
            dao=mock_dao,
            agent_id="test-agent",
            session_id="test-session",
            content="Some context",
            triplets=triplets,
            log_id=42,
        )

        # Ensure compute_embedding was called for head and tail
        assert mock_dao.vector_engine.compute_embedding.await_count == 2
        mock_dao.vector_engine.compute_embedding.assert_any_call("EntityA")
        mock_dao.vector_engine.compute_embedding.assert_any_call("EntityB")

        # Ensure memory nodes were inserted
        assert mock_dao.insert_memory.await_count == 2

        # Ensure edge was inserted with calculated epistemic_uncertainty
        # confidence = 0.8 -> epistemic_uncertainty = 0.2
        mock_dao.insert_edge.assert_awaited_once_with(
            "test-agent",
            source_id="node_head_id",
            target_id="node_tail_id",
            relation_type="related_to",
            weight=0.8,
            epistemic_uncertainty=0.19999999999999996,  # Due to float math 1.0 - 0.8
        )

    @pytest.mark.asyncio
    async def test_commit_raw_memory(self):
        mock_dao = MagicMock()
        mock_dao.vector_engine.compute_embedding = AsyncMock(return_value=[0.1] * 768)
        mock_dao.insert_memory = AsyncMock(return_value="raw_node_id")

        await _commit_raw_memory(
            dao=mock_dao,
            agent_id="test-agent",
            session_id="test-session",
            content="Raw content fallback",
            log_id=42,
        )

        # compute_embedding called once for the content
        mock_dao.vector_engine.compute_embedding.assert_awaited_once_with(
            "Raw content fallback"
        )

        # insert_memory called with node_type="MEMORY"
        mock_dao.insert_memory.assert_awaited_once()
        kwargs = mock_dao.insert_memory.call_args[1]
        assert kwargs["node_type"] == "MEMORY"
        assert kwargs["content"] == "Raw content fallback"


# ===================================================================
# Cold Path Execution
# ===================================================================


class TestProcessColdPath:
    @pytest.mark.asyncio
    @patch("mesa_workers.ingestion_worker._run_ecod_gate", new_callable=AsyncMock)
    @patch(
        "mesa_workers.ingestion_worker._run_rebel_extraction", new_callable=AsyncMock
    )
    @patch("mesa_workers.ingestion_worker._commit_triplets", new_callable=AsyncMock)
    async def test_successful_cold_path(
        self, mock_commit_triplets, mock_rebel, mock_ecod
    ):
        # ECOD passes
        mock_ecod.return_value = True

        # REBEL yields triplets
        mock_rebel.return_value = [
            {"head": "A", "relation": "R", "tail": "B", "confidence": "1.0"}
        ]

        mock_dao = MagicMock()
        mock_dao.get_raw_log = AsyncMock(
            return_value={
                "status": "queued",
                "payload": {"agent_id": "test-agent", "content": "A is R to B"},
            }
        )
        mock_dao.update_raw_log_status = AsyncMock()

        await process_cold_path(log_id=1, agent_id="test-agent", dao=mock_dao)

        # Verify status transitions
        # queued -> processing -> processed
        assert mock_dao.update_raw_log_status.await_count == 2
        mock_dao.update_raw_log_status.assert_any_call("test-agent", 1, "processing")
        mock_dao.update_raw_log_status.assert_any_call("test-agent", 1, "processed")

        # Verify commit triplets was called
        mock_commit_triplets.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("mesa_workers.ingestion_worker._run_ecod_gate", new_callable=AsyncMock)
    @patch(
        "mesa_workers.ingestion_worker._run_rebel_extraction", new_callable=AsyncMock
    )
    @patch("mesa_workers.ingestion_worker._commit_raw_memory", new_callable=AsyncMock)
    async def test_cold_path_no_triplets(self, mock_commit_raw, mock_rebel, mock_ecod):
        mock_ecod.return_value = True

        # REBEL yields nothing
        mock_rebel.return_value = []

        mock_dao = MagicMock()
        mock_dao.get_raw_log = AsyncMock(
            return_value={
                "status": "queued",
                "payload": {
                    "agent_id": "test-agent",
                    "content": "Just a general statement",
                },
            }
        )
        mock_dao.update_raw_log_status = AsyncMock()

        await process_cold_path(log_id=2, agent_id="test-agent", dao=mock_dao)

        mock_commit_raw.assert_awaited_once()
        assert mock_dao.update_raw_log_status.await_count == 2
        mock_dao.update_raw_log_status.assert_any_call("test-agent", 2, "processed")

    @pytest.mark.asyncio
    @patch("mesa_workers.ingestion_worker._run_ecod_gate", new_callable=AsyncMock)
    async def test_cold_path_ecod_reject(self, mock_ecod):
        # ECOD rejects (not novel)
        mock_ecod.return_value = False

        mock_dao = MagicMock()
        mock_dao.get_raw_log = AsyncMock(
            return_value={
                "status": "queued",
                "payload": {"agent_id": "test-agent", "content": "Not novel"},
            }
        )
        mock_dao.update_raw_log_status = AsyncMock()

        await process_cold_path(log_id=3, agent_id="test-agent", dao=mock_dao)

        # queued -> processing -> rejected
        assert mock_dao.update_raw_log_status.await_count == 2
        mock_dao.update_raw_log_status.assert_any_call("test-agent", 3, "processing")
        mock_dao.update_raw_log_status.assert_any_call(
            "test-agent", 3, "rejected", error_reason="ecod_novelty_below_threshold"
        )

    @pytest.mark.asyncio
    async def test_cold_path_safety_net_catch_all(self):
        mock_dao = MagicMock()
        mock_dao.get_raw_log = AsyncMock(side_effect=RuntimeError("Database down"))
        mock_dao.update_raw_log_status = AsyncMock()

        # Should not raise exception
        await process_cold_path(log_id=4, agent_id="test-agent", dao=mock_dao)

        # Status should be updated to failed
        mock_dao.update_raw_log_status.assert_awaited_once()
        args = mock_dao.update_raw_log_status.call_args
        assert args[0][0] == "test-agent"
        assert args[0][1] == 4
        assert args[0][2] == "failed"
        assert "RuntimeError: Database down" in args[1]["error_reason"]
