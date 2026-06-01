import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.consolidation.loop import llm_circuit_breaker
from mesa_storage.dao import MemoryDAO
from mesa_workers.ingestion_worker import process_cold_path

TEST_DIR = os.path.join(
    os.path.dirname(__file__), ".test_storage_tmp", "fault_tolerance"
)


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    # Reset circuit breaker before each test
    llm_circuit_breaker.failures = 0
    llm_circuit_breaker.last_failure_time = 0.0


def _make_mock_dao():
    dao = MagicMock(spec=MemoryDAO)
    dao.get_raw_log = AsyncMock(
        return_value={
            "id": 1,
            "payload": {
                "agent_id": "test_agent",
                "content": "Alice likes Bob.",
            },
            "status": "queued",
            "created_at": "2026-05-30T00:00:00Z",
        }
    )
    dao.update_raw_log_status = AsyncMock()
    dao.get_memories = AsyncMock(return_value=[])
    dao.insert_memory = AsyncMock(return_value="node_id")
    dao.insert_edge = AsyncMock(return_value="edge_id")
    dao.search_memory = AsyncMock(return_value=[])
    return dao


class HTTP429Error(Exception):
    pass


class HTTP503Error(Exception):
    pass


@pytest.mark.asyncio
async def test_exponential_backoff_success_on_4th_try():
    """Scenario 1: 3 failures (429), 4th succeeds -> successfully processed."""
    dao = _make_mock_dao()

    mock_adapter = AsyncMock(spec=BaseUniversalLLMAdapter)

    # Mocking the adapter to fail 3 times then succeed
    call_count = 0

    async def mock_acomplete(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            raise HTTP429Error("Rate Limited")
        return json.dumps([{"head": "Alice", "relation": "likes", "tail": "Bob"}])

    mock_adapter.acomplete = mock_acomplete

    with patch(
        "mesa_memory.adapter.factory.AdapterFactory.get_adapter",
        return_value=mock_adapter,
    ):
        with patch(
            "mesa_workers.ingestion_worker._get_rebel_extractor", return_value=None
        ):
            with patch(
                "mesa_workers.ingestion_worker._run_ecod_gate", return_value=True
            ):
                await process_cold_path(1, agent_id="test_agent", dao=dao)

    assert call_count == 4
    dao.update_raw_log_status.assert_any_await("test_agent", 1, "processing")
    dao.update_raw_log_status.assert_any_await("test_agent", 1, "processed")
    assert not llm_circuit_breaker.is_open
    assert llm_circuit_breaker.failures == 0


@pytest.mark.asyncio
async def test_circuit_breaker_trips_on_continuous_503():
    """Scenario 2: Continuous 503s trip the circuit breaker."""
    dao = _make_mock_dao()
    mock_adapter = AsyncMock(spec=BaseUniversalLLMAdapter)

    async def mock_acomplete_fail(*args, **kwargs):
        raise HTTP503Error("Service Unavailable")

    mock_adapter.acomplete = AsyncMock(side_effect=mock_acomplete_fail)

    with patch(
        "mesa_memory.adapter.factory.AdapterFactory.get_adapter",
        return_value=mock_adapter,
    ):
        with patch(
            "mesa_workers.ingestion_worker._get_rebel_extractor", return_value=None
        ):
            with patch(
                "mesa_workers.ingestion_worker._run_ecod_gate", return_value=True
            ):
                # 1st run: 5 failures
                await process_cold_path(1, agent_id="test_agent", dao=dao)
                assert llm_circuit_breaker.failures == 5

                # 2nd run: 5 failures -> total 10 -> breaker opens
                await process_cold_path(2, agent_id="test_agent", dao=dao)
                assert llm_circuit_breaker.failures == 10
                assert llm_circuit_breaker.is_open

                # 3rd run: should fail fast without calling the adapter
                call_count_before = mock_adapter.acomplete.call_count
                await process_cold_path(3, agent_id="test_agent", dao=dao)
                call_count_after = mock_adapter.acomplete.call_count

                assert (
                    call_count_before == call_count_after
                ), "Adapter should not be called when CB is open"

    # Verify that records were explicitly flagged as failed (DLQ in SQLite)
    failed_calls = [
        call
        for call in dao.update_raw_log_status.call_args_list
        if call.args[2] == "failed"
    ]
    assert len(failed_calls) == 3  # 3 runs failed
    assert "RetryError" in failed_calls[0].kwargs.get(
        "error_reason", ""
    ) or "HTTP503" in failed_calls[0].kwargs.get("error_reason", "")
