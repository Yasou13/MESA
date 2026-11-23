"""V4 cold-path extraction, deterministic validation, and failure handling."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_memory.extraction.service import FactCandidate, FactExtractionError
from mesa_workers.ingestion_worker import process_cold_path


class TestProcessColdPath:
    @pytest.mark.asyncio
    async def test_full_cognitive_extracts_canonical_candidate_before_projection(self):
        mock_dao = MagicMock()
        mock_dao.get_raw_log = AsyncMock(
            return_value={
                "status": "DEFERRED",
                "payload": {
                    "agent_id": "test-agent",
                    "session_id": "session-1",
                    "content": "Validated content",
                    "metadata": {"source": "test"},
                    "tenant_id": "tenant-v4",
                    "workspace_id": "workspace-v4",
                    "dataset_id": "dataset-v4",
                    "document_id": "document-v4",
                    "revision_id": "revision-v4",
                    "chunk_id": "chunk-v4",
                    "source_ref": "mcp-tool",
                    "validation_mode": 0,
                },
            }
        )
        mock_dao.update_raw_log_status = AsyncMock()
        mock_dao.record_mutation_extraction = AsyncMock(return_value=True)
        mock_dao.record_mutation_tier3_audit = AsyncMock()
        mock_dao.set_mutation_state = AsyncMock(return_value=True)
        extraction_service = MagicMock()

        async def extract(record):
            assert record["content_payload"] == "Validated content"
            assert record["raw_log_id"] == 17
            assert record["candidate_id"] == record["cmb_id"]
            assert record["tenant_id"] == "tenant-v4"
            assert record["workspace_id"] == "workspace-v4"
            assert record["dataset_id"] == "dataset-v4"
            assert record["document_id"] == "document-v4"
            assert record["revision_id"] == "revision-v4"
            assert record["chunk_id"] == "chunk-v4"
            assert record["source_ref"] == "mcp-tool"
            return [
                FactCandidate(
                    subject="MESA",
                    predicate="preserves",
                    object="Validated content",
                    fact_text="MESA preserves Validated content.",
                    confidence=0.95,
                )
            ]

        extraction_service.extract_facts_from_record = AsyncMock(side_effect=extract)

        await process_cold_path(
            log_id=17,
            agent_id="test-agent",
            dao=mock_dao,
            fact_extraction_service=extraction_service,
            model_processing_enabled=True,
        )

        mock_dao.record_mutation_extraction.assert_awaited_once()
        triplets = mock_dao.record_mutation_extraction.await_args.args[2]
        assert len(triplets) == 1
        assert triplets[0]["head"] == "MESA"
        mock_dao.update_raw_log_status.assert_any_call("test-agent", 17, "processed")

    @pytest.mark.asyncio
    async def test_extraction_failure_defers_for_retry_with_failure_class(self):
        mock_dao = MagicMock()
        mock_dao.get_raw_log = AsyncMock(
            return_value={
                "status": "DEFERRED",
                "payload": {
                    "agent_id": "test-agent",
                    "session_id": "session-1",
                    "content": "Temporary outage content",
                    "validation_mode": 0,
                },
            }
        )
        mock_dao.update_raw_log_status = AsyncMock()
        mock_dao.set_mutation_state = AsyncMock()
        extraction_service = MagicMock()
        extraction_service.extract_facts_from_record = AsyncMock(
            side_effect=FactExtractionError("provider timeout")
        )

        await process_cold_path(
            log_id=18,
            agent_id="test-agent",
            dao=mock_dao,
            fact_extraction_service=extraction_service,
            model_processing_enabled=True,
        )

        mock_dao.set_mutation_state.assert_awaited_once()
        assert mock_dao.set_mutation_state.await_args.args[2] == "RETRY_PENDING"
        assert (
            mock_dao.set_mutation_state.await_args.kwargs["failure_class"]
            == "Tier3Unavailable"
        )
        mock_dao.update_raw_log_status.assert_any_call("test-agent", 18, "DEFERRED")

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

    @pytest.mark.asyncio
    async def test_cold_path_status_update_failure(self):
        mock_dao = MagicMock()
        mock_dao.get_raw_log = AsyncMock(side_effect=RuntimeError("Database down"))
        mock_dao.update_raw_log_status = AsyncMock(
            side_effect=RuntimeError("Status update failed")
        )

        await process_cold_path(log_id=4, agent_id="test-agent", dao=mock_dao)
