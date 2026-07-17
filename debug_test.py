import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from mesa_memory.api.server import app
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from tests.utils.storage_helpers import insert_node


async def run_test() -> None:
    sqlite_eng = AsyncEngine(":memory:")
    await sqlite_eng.initialize()
    await initialize_schema(sqlite_eng)

    node_id = uuid.uuid4().hex
    await insert_node(
        sqlite_eng,
        node_id=node_id,
        entity_name="SearchTestNode",
        agent_id="agent-search",
        session_id="sess-001",
    )

    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock(return_value=[node_id])

    with patch("mesa_api.router.HybridRetriever", return_value=mock_retriever):
        client = TestClient(app)
        resp = client.post(
            "/v3/memory/search",
            json={
                "agent_id": "agent-search",
                "session_id": "sess-001",
                "query": "SearchTestNode",
            },
        )
        print("STATUS:", resp.status_code)
        print("BODY:", resp.json())


asyncio.run(run_test())
