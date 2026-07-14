import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mesa_workers.entity_consolidation_worker import (
    run_consolidation_scan,
    schedule_consolidation_worker,
)


@pytest.fixture
def mock_dao():
    dao = MagicMock()
    dao.get_memories = AsyncMock()
    dao.get_neighbors = AsyncMock()
    dao.get_memory_by_id = AsyncMock()
    dao.update_entity_description = AsyncMock()
    dao.get_all_active_agent_ids = AsyncMock()
    return dao


@pytest.fixture
def mock_llm_adapter():
    adapter = MagicMock()
    adapter.acomplete = AsyncMock()
    adapter.aembed = AsyncMock()
    return adapter


@pytest.mark.asyncio
async def test_run_consolidation_scan_no_entities(mock_dao, mock_llm_adapter):
    mock_dao.get_memories.return_value = []

    result = await run_consolidation_scan("agent_1", mock_dao, mock_llm_adapter)

    assert result["agent_id"] == "agent_1"
    assert result["processed"] == 0
    mock_dao.get_memories.assert_awaited_once_with("agent_1", include_consolidated=True)
    mock_dao.get_neighbors.assert_not_called()


@pytest.mark.asyncio
async def test_run_consolidation_scan_no_neighbors(mock_dao, mock_llm_adapter):
    mock_dao.get_memories.return_value = [{"id": "n1", "entity_name": "Entity1"}]
    mock_dao.get_neighbors.return_value = []

    result = await run_consolidation_scan("agent_1", mock_dao, mock_llm_adapter)

    assert result["processed"] == 0
    mock_dao.get_neighbors.assert_awaited_once_with(
        agent_id="agent_1", node_id="n1", max_hops=1, direction="both"
    )
    mock_llm_adapter.acomplete.assert_not_called()


@pytest.mark.asyncio
async def test_run_consolidation_scan_with_entities_and_neighbors(
    mock_dao, mock_llm_adapter
):
    # Mock entities
    mock_dao.get_memories.return_value = [{"id": "n1", "entity_name": "Entity1"}]

    # Mock neighbors for n1
    mock_dao.get_neighbors.return_value = [
        {"source_id": "n1", "target_id": "n2", "weight": 1.0, "agent_id": "agent_1"},
        {"source_id": "n3", "target_id": "n1", "weight": 1.0, "agent_id": "agent_1"},
    ]

    # Mock get_memory_by_id to resolve n2 and n3
    async def get_memory_mock(agent_id, node_id):
        if node_id == "n2":
            return {"entity_name": "Entity2", "type": "ENTITY"}
        elif node_id == "n3":
            return {"entity_name": "Entity3", "type": "EVENT"}
        return None

    mock_dao.get_memory_by_id.side_effect = get_memory_mock

    mock_llm_adapter.acomplete.return_value = "Consolidated description for Entity1"
    mock_llm_adapter.aembed.return_value = [0.1, 0.2, 0.3]

    result = await run_consolidation_scan("agent_1", mock_dao, mock_llm_adapter)

    assert result["processed"] == 1
    mock_dao.get_neighbors.assert_awaited_once_with(
        agent_id="agent_1", node_id="n1", max_hops=1, direction="both"
    )

    mock_llm_adapter.acomplete.assert_awaited_once()
    mock_llm_adapter.aembed.assert_awaited_once_with(
        "Consolidated description for Entity1"
    )

    mock_dao.update_entity_description.assert_awaited_once_with(
        agent_id="agent_1",
        node_id="n1",
        new_content="Consolidated description for Entity1",
        new_embedding=[0.1, 0.2, 0.3],
    )


@pytest.mark.asyncio
async def test_run_consolidation_scan_exception_handling(mock_dao, mock_llm_adapter):
    mock_dao.get_memories.return_value = [{"id": "n1", "entity_name": "Entity1"}]
    mock_dao.get_neighbors.return_value = [
        {"source_id": "n1", "target_id": "n2", "weight": 1.0, "agent_id": "agent_1"}
    ]
    mock_dao.get_memory_by_id.return_value = {
        "entity_name": "Entity2",
        "type": "ENTITY",
    }

    # Simulate an error during LLM call
    mock_llm_adapter.acomplete.side_effect = Exception("LLM Error")

    result = await run_consolidation_scan("agent_1", mock_dao, mock_llm_adapter)

    assert result["processed"] == 0
    # Should not crash, just catches exception and logs it
    mock_dao.update_entity_description.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_consolidation_worker(mock_dao, mock_llm_adapter):
    mock_dao.get_all_active_agent_ids.return_value = ["agent_1", "agent_2"]

    # We want to test that it loops at least once and then gets cancelled or we cancel it manually
    # Let's mock asyncio.sleep to raise CancelledError so it breaks the loop
    with (
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        patch(
            "mesa_workers.entity_consolidation_worker.run_consolidation_scan",
            new_callable=AsyncMock,
        ) as mock_scan,
    ):

        mock_sleep.side_effect = asyncio.CancelledError()

        await schedule_consolidation_worker(mock_dao, mock_llm_adapter, interval_sec=10)

        mock_dao.get_all_active_agent_ids.assert_awaited_once()
        assert mock_scan.await_count == 2
