"""Public opt-in surface contracts for bounded adaptive context retrieval."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import Depends, FastAPI, Request

import mesa_api.v4_router as v4_api
from mesa_api.v4_router import create_v4_router
from mesa_client.client import (
    AsyncMesaV4Client,
    MesaV4Client,
    MesaValidationError,
)
from mesa_mcp.adapter import MesaMCPAdapter
from mesa_mcp.configuration import MCPSettings
from mesa_mcp.errors import MCPError
from mesa_mcp.server import _tools
from mesa_mcp.v4_service import MesaHttpV4Service


def _candidate(candidate_id: str, evidence: str) -> dict:
    return {
        "entity": {"canonical_name": candidate_id},
        "candidate_id": candidate_id,
        "evidence_id": candidate_id,
        "assertion_id": candidate_id,
        "source_chunk_id": f"chunk-{candidate_id}",
        "document_id": f"document-{candidate_id}",
        "rrf_score": 1.0 / 61.0,
        "final_score": 1.0 / 61.0,
        "provenance": [
            {
                "assertion_id": candidate_id,
                "subject_name": candidate_id,
                "predicate": "states",
                "literal_value": evidence,
                "evidence_span": evidence,
            }
        ],
        "retrieval_provenance": {
            "origins": ["bm25"],
            "lane_ranks": {"bm25": 1},
            "raw_scores": {"bm25": 1.0},
        },
    }


def test_sync_client_sends_adaptive_mode_only_when_opted_in() -> None:
    client = MesaV4Client(base_url="http://mesa.invalid", api_key="test")
    request = MagicMock(return_value={})
    client._request = request

    client.get_context(session_id="session", query="q")
    default_params = request.call_args.kwargs["params"]
    assert "retrieval_mode" not in default_params

    client.get_context(session_id="session", query="q", retrieval_mode="adaptive")
    assert request.call_args.kwargs["params"]["retrieval_mode"] == "adaptive"


@pytest.mark.parametrize("invalid_mode", ["unsupported", []])
def test_sync_client_rejects_invalid_retrieval_mode(invalid_mode: object) -> None:
    client = MesaV4Client(base_url="http://mesa.invalid", api_key="test")
    request = MagicMock(return_value={})
    client._request = request

    with pytest.raises(MesaValidationError, match="retrieval_mode"):
        client.get_context(  # type: ignore[arg-type]
            session_id="session", query="q", retrieval_mode=invalid_mode
        )

    request.assert_not_called()


@pytest.mark.asyncio
async def test_async_client_sends_adaptive_mode_only_when_opted_in() -> None:
    client = AsyncMesaV4Client(base_url="http://mesa.invalid", api_key="test")
    request = AsyncMock(return_value={})
    client._request = request
    try:
        await client.get_context(session_id="session", query="q")
        default_params = request.await_args.kwargs["params"]
        assert "retrieval_mode" not in default_params

        await client.get_context(
            session_id="session", query="q", retrieval_mode="adaptive"
        )
        assert request.await_args.kwargs["params"]["retrieval_mode"] == "adaptive"
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_mode", ["unsupported", []])
async def test_async_client_rejects_invalid_retrieval_mode(
    invalid_mode: object,
) -> None:
    client = AsyncMesaV4Client(base_url="http://mesa.invalid", api_key="test")
    request = AsyncMock(return_value={})
    client._request = request
    try:
        with pytest.raises(MesaValidationError, match="retrieval_mode"):
            await client.get_context(  # type: ignore[arg-type]
                session_id="session", query="q", retrieval_mode=invalid_mode
            )

        request.assert_not_awaited()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_mcp_context_forwards_explicit_adaptive_mode() -> None:
    legacy = AsyncMock()
    v4 = AsyncMock()
    v4.v4_context.return_value = {"context": "ok"}
    adapter = MesaMCPAdapter(
        legacy,
        MCPSettings(api_key="test", use_v4=True),
        v4,
    )

    assert await adapter.get_context({"query": "q", "retrieval_mode": "adaptive"}) == {
        "context": "ok"
    }
    assert v4.v4_context.await_args.kwargs["retrieval_mode"] == "adaptive"


@pytest.mark.asyncio
async def test_mcp_context_rejects_non_string_retrieval_mode() -> None:
    adapter = MesaMCPAdapter(
        AsyncMock(),
        MCPSettings(api_key="test", use_v4=True),
        AsyncMock(),
    )

    with pytest.raises(MCPError, match="retrieval_mode"):
        await adapter.get_context({"query": "q", "retrieval_mode": []})


@pytest.mark.asyncio
async def test_legacy_mcp_context_does_not_silently_claim_adaptive_mode() -> None:
    adapter = MesaMCPAdapter(
        AsyncMock(),
        MCPSettings(api_key="test", use_v4=False),
    )

    with pytest.raises(MCPError) as raised:
        await adapter.get_context({"query": "q", "retrieval_mode": "adaptive"})

    assert raised.value.code == "UNIMPLEMENTED"


def test_mcp_context_schema_exposes_only_single_and_adaptive() -> None:
    tool = next(tool for tool in _tools() if tool.name == "mesa_get_context")

    assert tool.inputSchema["properties"]["retrieval_mode"] == {
        "type": "string",
        "enum": ["single", "adaptive"],
        "default": "single",
    }


@pytest.mark.asyncio
async def test_mcp_v4_service_forwards_adaptive_mode_to_client() -> None:
    client = AsyncMock()
    client.start_session.return_value = {"session_id": "session"}
    client.get_context.return_value = {"context": "ok"}
    service = MesaHttpV4Service(MCPSettings(api_key="test", use_v4=True))
    service._http_client = client

    await service.v4_context(query="q", retrieval_mode="adaptive")

    assert client.get_context.await_args.kwargs["retrieval_mode"] == "adaptive"


@pytest.mark.asyncio
async def test_api_adaptive_context_runs_planner_and_preserves_session_scope(
    monkeypatch,
) -> None:
    class FakeAdapter:
        calls = 0

        async def acomplete(self, _prompt, schema=None, **_kwargs):
            self.calls += 1
            return schema.model_validate({"queries": ["semantic rescue"]})

    fake_adapter = FakeAdapter()
    monkeypatch.setattr(v4_api.AdapterFactory, "get_adapter", lambda: fake_adapter)
    dao = MagicMock()
    dao.rebuild_admission.is_pending = AsyncMock(return_value=False)
    dao.get_v4_session = AsyncMock(
        return_value={
            "tenant_id": "tenant",
            "workspace_id": "workspace",
            "dataset_ids": ["dataset"],
            "agent_id": "agent",
            "session_id": "session",
            "status": "ACTIVE",
        }
    )
    dao.get_recent_logs = AsyncMock(return_value=[])
    dao.search_v4_memory = AsyncMock(
        side_effect=[
            [_candidate("weak", "weak evidence")],
            [_candidate("rescued", "RESCUED_CONTEXT")],
        ]
    )
    dao.list_session_mutation_summaries = AsyncMock(return_value=[])
    access = MagicMock()
    access.check_principal_session_access = AsyncMock(return_value=True)
    access.check_principal_permission = AsyncMock(return_value=True)
    access.check_access = AsyncMock(return_value=True)
    access.check_scope_role = AsyncMock(return_value=True)

    async def attach_principal(request: Request) -> None:
        request.state.principal = SimpleNamespace(
            principal_id="principal", principal_type="USER", status="active"
        )

    async def get_dao():
        return dao

    async def get_access():
        return access

    app = FastAPI(dependencies=[Depends(attach_principal)])
    app.include_router(create_v4_router(get_dao=get_dao, get_access_control=get_access))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=True),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/v4/sessions/session/context",
            params={"query": "original", "retrieval_mode": "adaptive"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "RESCUED_CONTEXT" in body["context"]
    assert body["context_diagnostics"]["retrieval_mode"] == "adaptive"
    assert body["context_diagnostics"]["retrieval_count"] == 2
    assert fake_adapter.calls == 1
    assert dao.search_v4_memory.await_count == 2
    assert {
        (
            call.kwargs["tenant_id"],
            call.kwargs["agent_id"],
            tuple(call.kwargs["dataset_ids"]),
            call.kwargs["jurisdiction"],
            call.kwargs["valid_at"],
            call.kwargs["valid_from"],
            call.kwargs["valid_to"],
        )
        for call in dao.search_v4_memory.await_args_list
    } == {("tenant", "agent", ("dataset",), None, None, None, None)}
