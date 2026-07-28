"""Regression coverage for retrieved score and content preservation."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mesa_api.router import create_memory_router
from mesa_api.schemas import MemorySearchRequest
from mesa_storage.dao import MemoryDAO


@pytest.mark.asyncio
async def test_search_response_preserves_retriever_score_and_memory_content() -> None:
    dao = SimpleNamespace(
        get_memory_by_id=AsyncMock(
            return_value={
                "entity_name": "Tesla",
                "content": "Q4 revenue was 25 billion dollars.",
                "node_type": "ENTITY",
                "agent_id": "analyst-1",
                "is_consolidated": True,
            }
        )
    )
    retriever = MagicMock()
    retriever.retrieve = AsyncMock(
        return_value={
            "cmb_ids": ["node-1"],
            "source_scores": {"node-1": 0.72},
            "diagnostics": {"degraded_sources": ["graph"]},
        }
    )
    access_control = MagicMock()
    access_control.check_access = AsyncMock(return_value=True)
    access_control.check_principal_session_access = AsyncMock(return_value=True)
    router = create_memory_router(
        get_dao=lambda: cast(MemoryDAO, dao),
        get_access_control=lambda: access_control,
    )
    endpoint = next(
        route.endpoint for route in router.routes if route.path == "/v3/memory/search"
    )

    with patch("mesa_api.router.HybridRetriever", return_value=retriever):
        response = await endpoint(
            request=SimpleNamespace(
                state=SimpleNamespace(
                    principal=SimpleNamespace(
                        principal_id="principal-a", status="active"
                    )
                )
            ),
            payload=MemorySearchRequest(
                agent_id="analyst-1",
                session_id="session-1",
                query="Tesla revenue",
            ),
            dao=dao,
        )

    assert response.retrieved_nodes[0].score == 0.72
    assert response.context == "Tesla: Q4 revenue was 25 billion dollars."
    assert response.degraded_sources == ["graph"]
