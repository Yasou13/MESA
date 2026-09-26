"""Exercise jurisdiction through HTTP/SDK/MCP into real SQLite retrieval."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Depends, FastAPI, Request

from mesa_api.v4_router import create_v4_router
from mesa_client.client import AsyncMesaV4Client
from mesa_mcp.adapter import MesaMCPAdapter
from mesa_mcp.configuration import MCPSettings
from mesa_mcp.v4_service import MesaHttpV4Service
from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["http", "sdk", "mcp"])
async def test_jurisdiction_reaches_sqlite_and_formatted_context(
    tmp_path, surface, monkeypatch
):
    engine = AsyncEngine(str(tmp_path / "jurisdiction.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, None, None)
    session = {
        "tenant_id": "tenant",
        "workspace_id": "workspace",
        "dataset_ids": ["dataset"],
        "agent_id": "agent",
        "session_id": "session",
        "status": "ACTIVE",
    }
    # Authorization/session services are deterministic; persistence, retrieval,
    # ContextBuilder, route, SDK, and MCP conversion are production code.
    monkeypatch.setattr(MemoryDAO, "get_v4_session", AsyncMock(return_value=session))
    monkeypatch.setattr(MemoryDAO, "get_recent_logs", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        MemoryDAO, "list_session_mutation_summaries", AsyncMock(return_value=[])
    )
    access = SimpleNamespace(
        check_principal_session_access=AsyncMock(return_value=True),
        check_access=AsyncMock(return_value=True),
        check_scope_role=AsyncMock(return_value=True),
    )

    async def attach_principal(request: Request):
        request.state.principal = SimpleNamespace(
            principal_id="principal", status="active"
        )

    app = FastAPI(dependencies=[Depends(attach_principal)])
    app.include_router(
        create_v4_router(
            get_dao=lambda: dao,
            get_access_control=lambda: access,
        )
    )
    try:
        for index, country in enumerate(("TR", "DE", "US"), start=1):
            record = MemoryCandidate.from_raw_log(
                raw_log_id=index,
                tenant_id="tenant",
                workspace_id="workspace",
                dataset_id="dataset",
                document_id=f"document-{country}",
                revision_id=f"revision-{country}",
                chunk_id=f"chunk-{country}",
                source_ref=f"source-{country}",
                agent_id="agent",
                session_id="session",
                content_payload=f"Shared policy country-{country}",
                metadata={"jurisdiction": country},
            ).as_consolidation_record()
            await dao.record_mutation(record, raw_log_id=index)
            mutation = await dao.get_projection_mutation(record["mutation_id"])
            assert mutation is not None
            await dao.project_v4_sql_entity(mutation=mutation, entity_name="Shared")
            await dao.project_v4_sql_assertion(
                mutation=mutation,
                triplet={
                    "head": "Shared",
                    "relation": "policy",
                    "literal_value": f"country-{country}",
                    "evidence_span": f"Shared policy country-{country}",
                    "jurisdiction": country,
                },
            )
            async with engine.transaction() as db:
                await db.execute(
                    "UPDATE memory_mutations SET state = 'COMMITTED' WHERE mutation_id = ?",
                    (mutation["mutation_id"],),
                )
                await db.commit()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://mesa.test",
        ) as http:
            sdk = AsyncMesaV4Client(base_url="http://mesa.test", api_key="test")
            await sdk._client.aclose()
            sdk._client = http
            if surface == "http":
                response = await http.get(
                    "/v4/sessions/session/context",
                    params={
                        "query": "Shared policy",
                        "jurisdiction": "TR",
                    },
                )
                assert response.status_code == 200, response.text
                result = response.json()
            elif surface == "sdk":
                result = await sdk.get_context(
                    session_id="session",
                    query="Shared policy",
                    jurisdiction="TR",
                )
            else:
                settings = MCPSettings(api_key="test", use_v4=True)
                service = MesaHttpV4Service(settings)
                await service._http_client.aclose()
                service._http_client = sdk
                service._get_session_id = AsyncMock(return_value="session")
                adapter = MesaMCPAdapter(AsyncMock(), settings, service)
                result = await adapter.get_context(
                    {
                        "query": "Shared policy",
                        "jurisdiction": "TR",
                    }
                )
            assert "country-TR" in result["context"]
            assert "country-DE" not in result["context"]
            assert "country-US" not in result["context"]
            assert len(result["canonical_memories"]) == 1
            assert {
                p["jurisdiction"]
                for memory in result["canonical_memories"]
                for p in memory["provenance"]
            } == {"TR"}
    finally:
        await engine.close()


def test_sync_sdk_omits_absent_temporal_query_parameters():
    from mesa_client.client import MesaV4Client

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"context": "ok"})

    with MesaV4Client(base_url="http://mesa.test", api_key="test") as sdk:
        sdk._client.close()
        sdk._client = httpx.Client(
            transport=httpx.MockTransport(respond), base_url="http://mesa.test"
        )
        assert sdk.get_context(
            session_id="session", query="policy", jurisdiction="TR"
        ) == {"context": "ok"}
    assert dict(requests[0].url.params) == {
        "query": "policy",
        "jurisdiction": "TR",
        "token_budget": "2048",
    }
