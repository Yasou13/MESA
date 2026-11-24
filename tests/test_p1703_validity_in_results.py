"""P17-03 contract tests: revision_number, valid_from, and valid_to across HTTP, SDK, and MCP (D88)."""

from __future__ import annotations

import json
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from mesa_client import AsyncMesaV4Client, V4ContextSource, V4SearchResult
from mesa_mcp.adapter import MesaMCPAdapter
from mesa_mcp.configuration import MCPSettings
from mesa_mcp.gateway.operations import GatewayOperationService
from mesa_mcp.v4_service import MesaHttpV4Service, _typed_result
from mesa_memory.api import server
from mesa_memory.security.untrusted_memory import TAG_CLOSE, TAG_OPEN


@pytest.fixture
def results_runtime(tmp_path, monkeypatch):
    """Combined runtime for HTTP/SDK/MCP result-validity contract checks."""

    @asynccontextmanager
    async def runtime():
        for key in (
            "MESA_MODEL_ENABLED",
            "MESA_EMBEDDING_ENABLED",
            "MESA_EXTRACTION_ENABLED",
            "MESA_EMBEDDING_MODEL",
            "MESA_LLM_MODEL",
        ):
            monkeypatch.delenv(key, raising=False)
        for key, value in {
            "MESA_RUNTIME_PROFILE": "combined",
            "MESA_STORAGE_ROOT": str(tmp_path / "storage"),
            "MESA_LOAD_DOTENV": "false",
            "MESA_EXTERNAL_PROVIDER_ENABLED": "false",
            "MESA_MODEL_ENABLED": "false",
            "MESA_EMBEDDING_ENABLED": "false",
            "MESA_EXTRACTION_ENABLED": "false",
            "MESA_API_KEY": "r24-p1703-key",
            "MESA_PRINCIPAL_ID": "r24-p1703-principal",
            "MESA_PRINCIPAL_STATUS": "active",
        }.items():
            monkeypatch.setenv(key, value)

        async def hold_polling(*args, **kwargs):
            import asyncio

            await asyncio.Event().wait()

        monkeypatch.setattr(server, "_run_combined_durable_consumer", hold_polling)

        async with AsyncExitStack() as stack:
            await stack.enter_async_context(server.lifespan(server.app))
            dao = server.state.dao
            await dao.ensure_v4_catalog_scope(
                tenant_id="validity-tenant", workspace_id="default", dataset_id="laws"
            )
            access = server.state.access_control
            await access.grant_principal_permission(
                "r24-p1703-principal", "validity-agent", "SESSION_CREATE"
            )
            await access.grant_scope_role(
                "r24-p1703-principal",
                tenant_id="validity-tenant",
                workspace_id="default",
                dataset_id="laws",
                role="WRITER",
            )
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=server.app),
                    base_url="http://test",
                    headers={"X-API-Key": "r24-p1703-key"},
                )
            )
            started = await client.post(
                "/v4/sessions/start",
                json={
                    "tenant_id": "validity-tenant",
                    "workspace_id": "default",
                    "dataset_ids": ["laws"],
                    "agent_id": "validity-agent",
                },
            )
            assert started.status_code == 201, started.text
            yield SimpleNamespace(
                dao=dao,
                client=client,
                session_id=started.json()["session_id"],
            )

    return runtime


async def _drain(env, max_steps: int = 30) -> None:
    for _ in range(max_steps):
        work = await server._consume_combined_durable_work_once(
            env.dao,
            fact_extraction_service=server.state.fact_extraction_service,
            model_processing_enabled=server.state.fact_extraction_service is not None,
        )
        if not work:
            break


@pytest.mark.asyncio
async def test_http_sdk_and_mcp_expose_revision_number_and_validity_dates(
    results_runtime,
) -> None:
    """Verify D88 fields across HTTP, AsyncMesaV4Client SDK, and MCP for dated & undated docs."""
    async with results_runtime() as env:
        # 1. Dated document with two revisions (v1 superseded by v2)
        r1 = await env.client.post(
            "/v4/documents",
            json={
                "session_id": env.session_id,
                "dataset_id": "laws",
                "document_id": "tax-law",
                "title": "Tax Law",
                "source_ref": "law://tax/v1",
                "content": "Kira vergisi oranı ilk sürümde yüzde on beş olarak belirlenmiştir.",
                "metadata": {"valid_from": "2020-01-01T00:00:00Z"},
            },
        )
        assert r1.status_code == 202
        assert r1.json()["revision_number"] == 1
        await _drain(env)

        r2 = await env.client.post(
            "/v4/documents",
            json={
                "session_id": env.session_id,
                "dataset_id": "laws",
                "document_id": "tax-law",
                "title": "Tax Law",
                "source_ref": "law://tax/v2",
                "content": "Kira vergisi oranı güncel sürümde yüzde yirmi olarak belirlenmiştir.",
                "metadata": {"valid_from": "2024-01-01T00:00:00Z"},
            },
        )
        assert r2.status_code == 202
        assert r2.json()["revision_number"] == 2
        await _drain(env)

        # 2. Undated document (valid_from and valid_to must be empty strings "")
        r_undated = await env.client.post(
            "/v4/documents",
            json={
                "session_id": env.session_id,
                "dataset_id": "laws",
                "document_id": "guide-doc",
                "title": "Guide",
                "source_ref": "file://guide.txt",
                "content": "Tarihsiz rehber metni genel bilgilendirme içerir.",
            },
        )
        assert r_undated.status_code == 202
        assert r_undated.json()["revision_number"] == 1
        await _drain(env)

        # --- Surface 1: HTTP /v4/memory/search ---
        http_hist = await env.client.post(
            "/v4/memory/search",
            json={
                "session_id": env.session_id,
                "query": "Kira vergisi oranı",
                "valid_at": "2022-06-01T00:00:00Z",
            },
        )
        assert http_hist.status_code == 200, http_hist.text
        hist_item = http_hist.json()["results"][0]
        assert hist_item["document_id"] == "tax-law"
        assert hist_item["revision_number"] == 1
        assert hist_item["valid_from"] == "2020-01-01T00:00:00Z"
        assert hist_item["valid_to"] == "2024-01-01T00:00:00Z"
        assert hist_item["provenance"][0]["revision_number"] == 1
        assert hist_item["provenance"][0]["valid_from"] == "2020-01-01T00:00:00Z"
        assert hist_item["provenance"][0]["valid_to"] == "2024-01-01T00:00:00Z"

        http_curr = await env.client.post(
            "/v4/memory/search",
            json={
                "session_id": env.session_id,
                "query": "Kira vergisi oranı",
            },
        )
        assert http_curr.status_code == 200
        curr_item = http_curr.json()["results"][0]
        assert curr_item["revision_number"] == 2
        assert curr_item["valid_from"] == "2024-01-01T00:00:00Z"
        assert curr_item["valid_to"] == ""

        http_undated = await env.client.post(
            "/v4/memory/search",
            json={
                "session_id": env.session_id,
                "query": "Tarihsiz rehber metni",
            },
        )
        assert http_undated.status_code == 200
        undated_item = http_undated.json()["results"][0]
        assert undated_item["document_id"] == "guide-doc"
        assert undated_item["revision_number"] == 1
        assert undated_item["valid_from"] == ""
        assert undated_item["valid_to"] == ""

        # --- Surface 1b: HTTP GET /v4/sessions/{session_id}/context ---
        http_ctx = await env.client.get(
            f"/v4/sessions/{env.session_id}/context",
            params={
                "query": "Kira vergisi oranı",
                "valid_at": "2022-06-01T00:00:00Z",
            },
        )
        assert http_ctx.status_code == 200, http_ctx.text
        ctx_body = http_ctx.json()
        ctx_source = ctx_body["canonical_memories"][0]
        assert ctx_source["document_id"] == "tax-law"
        assert ctx_source["revision_number"] == 1
        assert ctx_source["valid_from"] == "2020-01-01T00:00:00Z"
        assert ctx_source["valid_to"] == "2024-01-01T00:00:00Z"
        assert ctx_source["provenance"][0]["revision_number"] == 1
        assert ctx_source["provenance"][0]["valid_from"] == "2020-01-01T00:00:00Z"
        assert ctx_source["provenance"][0]["valid_to"] == "2024-01-01T00:00:00Z"
        assert "2020-01-01T00:00:00Z" in ctx_body["context"]
        assert "2024-01-01T00:00:00Z" in ctx_body["context"]

        http_ctx_undated = await env.client.get(
            f"/v4/sessions/{env.session_id}/context",
            params={"query": "Tarihsiz rehber metni"},
        )
        assert http_ctx_undated.status_code == 200
        undated_source = http_ctx_undated.json()["canonical_memories"][0]
        assert undated_source["revision_number"] == 1
        assert undated_source["valid_from"] == ""
        assert undated_source["valid_to"] == ""

        # --- Surface 2: SDK (AsyncMesaV4Client) ---
        sdk = AsyncMesaV4Client(base_url="http://test", api_key="r24-p1703-key")
        sdk._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://test",
            headers={"X-API-Key": "r24-p1703-key"},
        )
        try:
            sdk_search = await sdk.search(
                session_id=env.session_id,
                query="Kira vergisi oranı",
                valid_at="2022-06-01T00:00:00Z",
            )
            sdk_res: V4SearchResult = sdk_search["results"][0]
            assert sdk_res["revision_number"] == 1
            assert sdk_res["valid_from"] == "2020-01-01T00:00:00Z"
            assert sdk_res["valid_to"] == "2024-01-01T00:00:00Z"

            sdk_ctx = await sdk.get_context(
                session_id=env.session_id,
                query="Kira vergisi oranı",
                valid_at="2022-06-01T00:00:00Z",
            )
            sdk_src: V4ContextSource = sdk_ctx["canonical_memories"][0]
            assert sdk_src["revision_number"] == 1
            assert sdk_src["valid_from"] == "2020-01-01T00:00:00Z"
            assert sdk_src["valid_to"] == "2024-01-01T00:00:00Z"

            # --- Surface 3: MCP (MesaHttpV4Service + MesaMCPAdapter + Gateway) ---
            mcp_settings = MCPSettings(
                base_url="http://test",
                api_key="r24-p1703-key",
                default_tenant_id="validity-tenant",
                default_workspace_id="default",
                default_dataset_id="laws",
                actor_id="validity-agent",
            )
            v4_service = MesaHttpV4Service(mcp_settings)
            v4_service._http_client = sdk
            adapter = MesaMCPAdapter(AsyncMock(), mcp_settings, v4_service)

            mcp_hist = await adapter.mesa_recall(
                {
                    "dataset_id": "laws",
                    "query": "Kira vergisi oranı",
                    "valid_at": "2022-06-01T00:00:00Z",
                }
            )
            assert mcp_hist["total"] >= 1
            mcp_item = mcp_hist["results"][0]
            assert mcp_item["document_id"] == "tax-law"
            assert mcp_item["revision_number"] == 1
            assert mcp_item["valid_from"] == "2020-01-01T00:00:00Z"
            assert mcp_item["valid_to"] == "2024-01-01T00:00:00Z"
            assert TAG_OPEN in mcp_item["content"] and TAG_CLOSE in mcp_item["content"]

            mcp_undated = await adapter.mesa_recall(
                {"dataset_id": "laws", "query": "Tarihsiz rehber metni"}
            )
            mcp_undated_item = mcp_undated["results"][0]
            assert mcp_undated_item["revision_number"] == 1
            assert mcp_undated_item["valid_from"] == ""
            assert mcp_undated_item["valid_to"] == ""

            # Gateway formatter preserves D88 fields without leaking raw text (D23)
            typed = _typed_result(hist_item)
            gw_formatted = GatewayOperationService._format_gateway_memory(typed)
            assert gw_formatted["revision_number"] == 1
            assert gw_formatted["valid_from"] == "2020-01-01T00:00:00Z"
            assert gw_formatted["valid_to"] == "2024-01-01T00:00:00Z"
            assert TAG_OPEN in gw_formatted["content"]
        finally:
            await sdk.aclose()


def test_openapi_snapshot_documents_validity_result_fields() -> None:
    """Verify both live OpenAPI and docs/reference/openapi.json expose D88 fields."""
    live = server.app.openapi()
    snapshot_path = Path(__file__).resolve().parents[1] / "docs" / "reference" / "openapi.json"
    saved = json.loads(snapshot_path.read_text(encoding="utf-8"))

    for spec_name, spec in (("live", live), ("snapshot", saved)):
        schemas = spec["components"]["schemas"]
        assert "V4SearchResultItem" in schemas, spec_name
        item_props = schemas["V4SearchResultItem"]["properties"]
        assert "revision_number" in item_props, spec_name
        assert "valid_from" in item_props, spec_name
        assert "valid_to" in item_props, spec_name

        search_items = schemas["V4SearchResponse"]["properties"]["results"]["items"]
        assert search_items == {"$ref": "#/components/schemas/V4SearchResultItem"}, spec_name

        assert "V4ContextResponse" in schemas, spec_name
        ctx_items = schemas["V4ContextResponse"]["properties"]["canonical_memories"]["items"]
        assert ctx_items == {"$ref": "#/components/schemas/V4SearchResultItem"}, spec_name

    assert saved == live
