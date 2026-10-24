"""V4 cold-path validation and failure handling."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_workers.ingestion_worker import process_cold_path


class TestProcessColdPath:
    @pytest.mark.asyncio
    async def test_full_cognitive_validates_canonical_candidate_before_projection(self):
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
                },
            }
        )
        mock_dao.update_raw_log_status = AsyncMock()
        loop = MagicMock()

        async def accept(records):
            record = records[0]
            assert record["content_payload"] == "Validated content"
            assert record["tier3_deferred"] is True
            assert record["raw_log_id"] == 17
            assert record["candidate_id"] == record["cmb_id"]
            assert record["tenant_id"] == "tenant-v4"
            assert record["workspace_id"] == "workspace-v4"
            assert record["dataset_id"] == "dataset-v4"
            assert record["document_id"] == "document-v4"
            assert record["revision_id"] == "revision-v4"
            assert record["chunk_id"] == "chunk-v4"
            assert record["source_ref"] == "mcp-tool"
            return {
                "accepted": [record["candidate_id"]],
                "rejected": [],
                "deferred": [],
            }

        loop.run_batch = AsyncMock(side_effect=accept)

        await process_cold_path(
            log_id=17,
            agent_id="test-agent",
            dao=mock_dao,
            consolidation_loop=loop,
            model_processing_enabled=True,
            require_tier3_validation=True,
        )

        mock_dao.update_raw_log_status.assert_any_call("test-agent", 17, "processed")

    @pytest.mark.asyncio
    async def test_full_cognitive_update_is_deferred_to_tier3(self):
        """Potentially contradictory updates must receive the selected validation policy."""
        mock_dao = MagicMock()
        mock_dao.get_raw_log = AsyncMock(
            return_value={
                "status": "DEFERRED",
                "payload": {
                    "agent_id": "test-agent",
                    "session_id": "session-1",
                    "content": "The policy now permits remote work.",
                    "validation_mode": 0,
                },
            }
        )
        mock_dao.update_raw_log_status = AsyncMock()
        loop = MagicMock()

        async def accept(records):
            return {
                "accepted": [records[0]["candidate_id"]],
                "rejected": [],
                "deferred": [],
            }

        loop.run_batch = AsyncMock(side_effect=accept)

        await process_cold_path(
            log_id=19,
            agent_id="test-agent",
            dao=mock_dao,
            consolidation_loop=loop,
            model_processing_enabled=True,
            require_tier3_validation=True,
        )

        loop.run_batch.assert_awaited_once()
        mock_dao.update_raw_log_status.assert_any_call("test-agent", 19, "processed")
        assert all(
            call.args[2] != "rejected"
            for call in mock_dao.update_raw_log_status.await_args_list
        )

    @pytest.mark.asyncio
    async def test_full_cognitive_rejection_creates_no_legacy_projection(self):
        mock_dao = MagicMock()
        mock_dao.get_raw_log = AsyncMock(
            return_value={
                "status": "DEFERRED",
                "payload": {
                    "agent_id": "test-agent",
                    "session_id": "session-1",
                    "content": "Rejected content",
                    "validation_mode": 0,
                },
            }
        )
        mock_dao.update_raw_log_status = AsyncMock()
        loop = MagicMock()

        async def reject(records):
            return {
                "accepted": [],
                "rejected": [records[0]["candidate_id"]],
                "deferred": [],
            }

        loop.run_batch = AsyncMock(side_effect=reject)

        await process_cold_path(
            log_id=18,
            agent_id="test-agent",
            dao=mock_dao,
            consolidation_loop=loop,
            require_tier3_validation=True,
        )

        mock_dao.update_raw_log_status.assert_any_call(
            "test-agent", 18, "rejected", error_reason="tier3_validation_rejected"
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

    @pytest.mark.asyncio
    async def test_cold_path_status_update_failure(self):
        mock_dao = MagicMock()
        mock_dao.get_raw_log = AsyncMock(side_effect=RuntimeError("Database down"))
        mock_dao.update_raw_log_status = AsyncMock(
            side_effect=RuntimeError("Status update failed")
        )

        await process_cold_path(log_id=4, agent_id="test-agent", dao=mock_dao)
