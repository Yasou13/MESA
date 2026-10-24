"""Cold-path payload and status guards."""

from unittest.mock import AsyncMock

import pytest

from mesa_workers.ingestion_worker import process_cold_path


class TestProcessColdPath:
    """process_cold_path: tests the main entry point with mocked DAO."""

    @pytest.mark.asyncio
    async def test_skip_when_log_not_found(self):
        """Should return early if raw_log doesn't exist."""
        mock_dao = AsyncMock()
        mock_dao.get_raw_log.return_value = None

        # Should not raise
        await process_cold_path(log_id=999, agent_id="test", dao=mock_dao)
        mock_dao.get_raw_log.assert_awaited_once_with("test", 999)
        # Should NOT call update_raw_log_status since log was not found
        mock_dao.update_raw_log_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skip_when_status_not_deferred(self):
        """Should skip processing when status is not DEFERRED."""
        mock_dao = AsyncMock()
        mock_dao.get_raw_log.return_value = {"status": "processed", "payload": {}}

        await process_cold_path(log_id=1, agent_id="test", dao=mock_dao)
        # Should NOT transition status since it's already processed
        mock_dao.update_raw_log_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reject_missing_content(self):
        """Should reject entries with missing content field."""
        mock_dao = AsyncMock()
        mock_dao.get_raw_log.return_value = {
            "status": "DEFERRED",
            "payload": {"agent_id": "test_agent", "content": ""},
        }

        await process_cold_path(log_id=2, agent_id="test", dao=mock_dao)
        # Should mark as rejected due to missing content
        mock_dao.update_raw_log_status.assert_awaited()
        call_args = mock_dao.update_raw_log_status.call_args
        assert call_args[0][2] == "rejected"
