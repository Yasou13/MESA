"""P18-01 tests: session-optional search and document ingest (D94)."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest

from mesa_client.client import AsyncMesaV4Client, MesaV4Client
from mesa_mcp.adapter import MesaMCPAdapter
from mesa_mcp.configuration import MCPSettings
from mesa_mcp.errors import MCPError
from mesa_mcp.v4_service import MesaHttpV4Service
from mesa_memory.api import server


async def _drain_all_work(dao, max_steps: int = 30) -> None:
    """Drain queued cold-path jobs and SQL/VECTOR projections deterministically."""
    for _ in range(max_steps):
        work = await server._consume_combined_durable_work_once(
            dao,
            fact_extraction_service=server.state.fact_extraction_service,
            model_processing_enabled=server.state.fact_extraction_service is not None,
        )
        if not work:
            break


@pytest.fixture
def d94_runtime(tmp_path, monkeypatch):
    @asynccontextmanager
    async def runtime():
        for key in (
            "MESA_MODEL_ENABLED",
            "MESA_EMBEDDING_ENABLED",
            "MESA_EXTRACTION_ENABLED",
            "MESA_EMBEDDING_MODEL",
            "MESA_EMBEDDING_DIMENSION",
            "MESA_LLM_MODEL",
        ):
            monkeypatch.delenv(key, raising=False)
        env_vars = {
            "MESA_RUNTIME_PROFILE": "combined",
            "MESA_STORAGE_ROOT": str(tmp_path / "storage"),
            "MESA_LOAD_DOTENV": "false",
            "MESA_EXTERNAL_PROVIDER_ENABLED": "false",
            "MESA_MODEL_ENABLED": "false",
            "MESA_EMBEDDING_ENABLED": "false",
            "MESA_EXTRACTION_ENABLED": "false",
            "MESA_API_KEY": "owner-bootstrap-key",
            "MESA_PRINCIPAL_ID": "owner-principal",
            "MESA_PRINCIPAL_STATUS": "active",
        }
        for key, value in env_vars.items():
            monkeypatch.setenv(key, value)

        async def hold_polling(*args, **kwargs):
            await asyncio.Event().wait()

        monkeypatch.setattr(server, "_run_combined_durable_consumer", hold_polling)

        async with AsyncExitStack() as stack:
            await stack.enter_async_context(server.lifespan(server.app))
            dao = server.state.dao
            access = server.state.access_control
            key_store = server.state.api_key_store

            await dao.ensure_v4_catalog_scope(
                tenant_id="t1", workspace_id="ws1", dataset_id="ds1"
            )
            await dao.ensure_v4_catalog_scope(
                tenant_id="t1", workspace_id="ws2", dataset_id="ds2"
            )
            await dao.ensure_v4_catalog_scope(
                tenant_id="local", workspace_id="default", dataset_id="default"
            )

            writer_key = await key_store.issue_key(principal_id="writer-principal")
            reader_key = await key_store.issue_key(principal_id="reader-principal")
            ws1_reader_key = await key_store.issue_key(principal_id="ws1-reader")
            outsider_key = await key_store.issue_key(principal_id="outsider-principal")

            await access.grant_scope_role(
                "writer-principal",
                tenant_id="t1",
                workspace_id="ws1",
                dataset_id="ds1",
                role="WRITER",
            )
            await access.grant_scope_role(
                "writer-principal",
                tenant_id="local",
                workspace_id="default",
                dataset_id="default",
                role="WRITER",
            )
            await access.grant_principal_permission(
                "writer-principal", "local-agent", "SESSION_CREATE"
            )
            await access.grant_principal_permission(
                "writer-principal", "custom-agent", "SESSION_CREATE"
            )
            await access.grant_scope_role(
                "reader-principal",
                tenant_id="t1",
                workspace_id="ws1",
                dataset_id="ds1",
                role="READER",
            )
            await access.grant_scope_role(
                "reader-principal",
                tenant_id="local",
                workspace_id="default",
                dataset_id="default",
                role="READER",
            )
            await access.grant_scope_role(
                "ws1-reader",
                tenant_id="t1",
                workspace_id="ws1",
                role="READER",
            )
            # R25 amendment A1: sessionless calls need the agent permission.
            for principal in ("reader-principal", "ws1-reader"):
                await access.grant_principal_permission(
                    principal, "local-agent", "SESSION_CREATE"
                )

            def make_client(api_key: str) -> httpx.AsyncClient:
                return httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=server.app),
                    base_url="http://test",
                    headers={"X-API-Key": api_key},
                )

            writer = await stack.enter_async_context(make_client(writer_key))
            reader = await stack.enter_async_context(make_client(reader_key))
            ws1_reader = await stack.enter_async_context(make_client(ws1_reader_key))
            outsider = await stack.enter_async_context(make_client(outsider_key))

            yield {
                "dao": dao,
                "writer": writer,
                "reader": reader,
                "ws1_reader": ws1_reader,
                "outsider": outsider,
                "writer_key": writer_key,
                "reader_key": reader_key,
            }

    return runtime


@pytest.mark.asyncio
async def test_sessionless_ingest_and_search_roles(d94_runtime) -> None:
    async with d94_runtime() as env:
        dao = env["dao"]
        writer = env["writer"]
        reader = env["reader"]
        ws1_reader = env["ws1_reader"]
        outsider = env["outsider"]

        # 1. WRITER can ingest a document without a session_id.
        ingest = await writer.post(
            "/v4/documents",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_id": "ds1",
                "document_id": "doc-no-sess",
                "title": "Lease Terms",
                "source_ref": "file://lease.txt",
                "content": "Monthly rent is due on the fifteenth day of each month.",
            },
        )
        assert ingest.status_code == 202, ingest.text
        body = ingest.json()
        assert body["document_id"] == "doc-no-sess"
        assert body["chunk_count"] == 1
        mutation_id = body["mutation_ids"][0]

        await _drain_all_work(dao)

        # Mutation status is readable by READER and WRITER, denied to outsider.
        mut_reader = await reader.get(f"/v4/mutations/{mutation_id}")
        assert mut_reader.status_code == 200, mut_reader.text
        assert mut_reader.json()["state"] == "COMMITTED"
        mut_outsider = await outsider.get(f"/v4/mutations/{mutation_id}")
        assert mut_outsider.status_code == 403

        # 2. READER can search without a session_id and receives session_id=None.
        search = await reader.post(
            "/v4/memory/search",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_ids": ["ds1"],
                "query": "fifteenth day monthly rent",
            },
        )
        assert search.status_code == 200, search.text
        search_body = search.json()
        assert search_body["session_id"] is None
        assert search_body["dataset_ids"] == ["ds1"]
        assert any(
            item["document_id"] == "doc-no-sess" for item in search_body["results"]
        )

        # 3. READER cannot ingest (403).
        reader_ingest = await reader.post(
            "/v4/documents",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_id": "ds1",
                "document_id": "doc-reader-blocked",
                "title": "Blocked",
                "source_ref": "file://blocked.txt",
                "content": "Readers may not write documents.",
            },
        )
        assert reader_ingest.status_code == 403

        # 4. Principal without dataset role gets 403 on search and ingest.
        outsider_search = await outsider.post(
            "/v4/memory/search",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_ids": ["ds1"],
                "query": "rent",
            },
        )
        assert outsider_search.status_code == 403
        outsider_ingest = await outsider.post(
            "/v4/documents",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_id": "ds1",
                "document_id": "doc-outsider",
                "title": "Outsider",
                "source_ref": "file://outsider.txt",
                "content": "Outsiders may not write.",
            },
        )
        assert outsider_ingest.status_code == 403

        # 5. Workspace-level READER on ws1 cannot search ds2 (belongs to ws2).
        cross_ws = await ws1_reader.post(
            "/v4/memory/search",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_ids": ["ds2"],
                "query": "rent",
            },
        )
        assert cross_ws.status_code == 403


@pytest.mark.asyncio
async def test_sessionless_sees_cli_ingested_data_and_validates_mixes(d94_runtime) -> None:
    async with d94_runtime() as env:
        dao = env["dao"]
        writer = env["writer"]
        reader = env["reader"]

        # Simulate CLI ingest: session started with agent_id="local-agent".
        cli_sess = await writer.post(
            "/v4/sessions/start",
            json={
                "tenant_id": "local",
                "workspace_id": "default",
                "dataset_ids": ["default"],
                "agent_id": "local-agent",
            },
        )
        assert cli_sess.status_code == 201
        cli_session_id = cli_sess.json()["session_id"]

        cli_ingest = await writer.post(
            "/v4/documents",
            json={
                "session_id": cli_session_id,
                "dataset_id": "default",
                "document_id": "doc-cli",
                "title": "CLI Handbook",
                "source_ref": "file://cli.txt",
                "content": "CLI ingested evidence about local storage defaults.",
            },
        )
        assert cli_ingest.status_code == 202
        await _drain_all_work(dao)

        # Sessionless search without agent_id defaults to local-agent and sees CLI data.
        res = await reader.post(
            "/v4/memory/search",
            json={
                "tenant_id": "local",
                "workspace_id": "default",
                "dataset_ids": ["default"],
                "query": "local storage defaults",
            },
        )
        assert res.status_code == 200, res.text
        assert any(item["document_id"] == "doc-cli" for item in res.json()["results"])

        # Missing required explicit scope fields when session_id is omitted -> 422.
        for bad_search in (
            {"workspace_id": "default", "dataset_ids": ["default"], "query": "x"},
            {"tenant_id": "local", "dataset_ids": ["default"], "query": "x"},
            {"tenant_id": "local", "workspace_id": "default", "query": "x"},
        ):
            r = await reader.post("/v4/memory/search", json=bad_search)
            assert r.status_code == 422, (bad_search, r.text)

        for bad_doc in (
            {
                "workspace_id": "default",
                "dataset_id": "default",
                "document_id": "d1",
                "title": "T",
                "source_ref": "s",
                "content": "hello",
            },
            {
                "tenant_id": "local",
                "dataset_id": "default",
                "document_id": "d1",
                "title": "T",
                "source_ref": "s",
                "content": "hello",
            },
        ):
            r = await writer.post("/v4/documents", json=bad_doc)
            assert r.status_code == 422, (bad_doc, r.text)

        # Mixing session_id with disagreeing explicit scope -> 422.
        for bad_mix in (
            {"session_id": cli_session_id, "tenant_id": "t1", "query": "defaults"},
            {"session_id": cli_session_id, "workspace_id": "ws1", "query": "defaults"},
            {
                "session_id": cli_session_id,
                "agent_id": "other-agent",
                "query": "defaults",
            },
        ):
            r = await writer.post("/v4/memory/search", json=bad_mix)
            assert r.status_code == 422, (bad_mix, r.text)

        bad_doc_mix = await writer.post(
            "/v4/documents",
            json={
                "session_id": cli_session_id,
                "tenant_id": "t1",
                "dataset_id": "default",
                "document_id": "doc-bad-mix",
                "title": "Mismatch",
                "source_ref": "file://mismatch.txt",
                "content": "Should be rejected.",
            },
        )
        assert bad_doc_mix.status_code == 422

        # Agreeing session_id + explicit scope succeeds.
        agree = await writer.post(
            "/v4/memory/search",
            json={
                "session_id": cli_session_id,
                "tenant_id": "local",
                "workspace_id": "default",
                "agent_id": "local-agent",
                "dataset_ids": ["default"],
                "query": "local storage defaults",
            },
        )
        assert agree.status_code == 200
        assert agree.json()["session_id"] == cli_session_id

        # /v4/memory/insert still requires session_id (422 without it).
        insert_no_sess = await writer.post(
            "/v4/memory/insert",
            json={
                "dataset_id": "default",
                "document_id": "doc-raw",
                "revision_id": "rev-raw",
                "chunk_id": "chunk-raw",
                "title": "Raw",
                "source_ref": "raw",
                "content": "Agent memory needs a session.",
            },
        )
        assert insert_no_sess.status_code == 422


@pytest.mark.asyncio
async def test_sdk_sync_and_async_sessionless(d94_runtime) -> None:
    async with d94_runtime() as env:
        dao = env["dao"]
        writer_key = env["writer_key"]
        reader_key = env["reader_key"]

        # Async SDK against real ASGI app.
        async_writer = AsyncMesaV4Client(base_url="http://test", api_key=writer_key)
        async_writer._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://test",
            headers={"X-API-Key": writer_key},
        )
        async_reader = AsyncMesaV4Client(base_url="http://test", api_key=reader_key)
        async_reader._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://test",
            headers={"X-API-Key": reader_key},
        )
        try:
            added = await async_writer.add_document(
                tenant_id="t1",
                workspace_id="ws1",
                dataset_id="ds1",
                document_id="doc-sdk-async",
                title="SDK Async Doc",
                source_ref="file://async.txt",
                content="Asynchronous SDK document ingested without a session.",
            )
            assert added["document_id"] == "doc-sdk-async"
            await _drain_all_work(dao)

            found = await async_reader.search(
                tenant_id="t1",
                workspace_id="ws1",
                dataset_ids=["ds1"],
                query="Asynchronous SDK document",
            )
            assert found["session_id"] is None
            assert any(
                item["document_id"] == "doc-sdk-async" for item in found["results"]
            )
        finally:
            await async_writer.aclose()
            await async_reader.aclose()

    # Sync SDK payload shape check via mock transport.
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen.append((request.url.path, _json.loads(request.content.decode("utf-8"))))
        if request.url.path == "/v4/documents":
            return httpx.Response(
                202,
                json={
                    "document_id": "doc-sync",
                    "revision_id": "rev-sync",
                    "revision_number": 1,
                    "chunk_count": 1,
                    "mutation_ids": ["m1"],
                    "duplicate": False,
                },
            )
        return httpx.Response(
            200,
            json={"session_id": None, "dataset_ids": ["ds1"], "results": []},
        )

    sync_client = MesaV4Client(base_url="http://test", api_key="k")
    sync_client._client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    try:
        sync_client.add_document(
            tenant_id="t1",
            workspace_id="ws1",
            dataset_id="ds1",
            document_id="doc-sync",
            title="Sync",
            source_ref="file://sync.txt",
            content="Sync text",
        )
        sync_client.search(
            tenant_id="t1",
            workspace_id="ws1",
            dataset_ids=["ds1"],
            query="Sync text",
        )
    finally:
        sync_client.close()

    assert seen[0][0] == "/v4/documents"
    assert "session_id" not in seen[0][1]
    assert seen[0][1]["tenant_id"] == "t1"
    assert seen[0][1]["workspace_id"] == "ws1"
    assert seen[1][0] == "/v4/memory/search"
    assert "session_id" not in seen[1][1]
    assert seen[1][1]["dataset_ids"] == ["ds1"]


@pytest.mark.asyncio
async def test_mcp_sessionless_recall_for_reader_only_principal(d94_runtime) -> None:
    async with d94_runtime() as env:
        dao = env["dao"]
        writer = env["writer"]
        reader_key = env["reader_key"]

        await writer.post(
            "/v4/documents",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_id": "ds1",
                "document_id": "doc-mcp-reader",
                "title": "MCP Doc",
                "source_ref": "file://mcp.txt",
                "content": "Evidence visible to READER-only MCP recall without a session.",
            },
        )
        await _drain_all_work(dao)

        settings = MCPSettings(
            base_url="http://test",
            api_key=reader_key,
            default_tenant_id="t1",
            default_workspace_id="ws1",
            default_dataset_id="ds1",
            actor_id="local-agent",
        )
        v4_svc = MesaHttpV4Service(settings)
        v4_svc._http_client._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://test",
            headers={"X-API-Key": reader_key},
        )
        adapter = MesaMCPAdapter(AsyncMock(), settings, v4_svc)
        try:
            recalled = await adapter.mesa_recall(
                {
                    "query": "READER-only MCP recall",
                    "dataset_ids": ["ds1"],
                }
            )
            assert recalled["total"] == 1
            assert recalled["results"][0]["document_id"] == "doc-mcp-reader"

            with pytest.raises(MCPError, match="disagree"):
                await adapter.mesa_recall(
                    {
                        "query": "mismatch",
                        "dataset_id": "ds2",
                        "dataset_ids": ["ds1"],
                    }
                )
        finally:
            await v4_svc.close()


@pytest.mark.asyncio
async def test_sessionless_requires_agent_permission_amendment_a1(d94_runtime) -> None:
    """R25 amendment A1: sessionless calls pass the /v4/sessions/start agent check."""
    async with d94_runtime() as env:
        writer = env["writer"]
        scope = {"tenant_id": "t1", "workspace_id": "ws1"}

        def document(agent_id: str, document_id: str) -> dict:
            return {
                **scope,
                "dataset_id": "ds1",
                "agent_id": agent_id,
                "document_id": document_id,
                "title": "Agent scoped",
                "source_ref": f"file://{document_id}.txt",
                "content": "Agent scoped quartz evidence.",
            }

        def search(agent_id: str) -> dict:
            return {
                **scope,
                "dataset_ids": ["ds1"],
                "agent_id": agent_id,
                "query": "quartz evidence",
            }

        # The WRITER has dataset rights but no permission for agent X.
        denied_session = await writer.post(
            "/v4/sessions/start",
            json={**scope, "dataset_ids": ["ds1"], "agent_id": "agent-x"},
        )
        assert denied_session.status_code == 403
        denied_write = await writer.post("/v4/documents", json=document("agent-x", "x1"))
        assert denied_write.status_code == 403, denied_write.text
        fields = {k: v for k, v in document("agent-x", "x2").items() if k != "content"}
        denied_upload = await writer.post(
            "/v4/documents/upload",
            data=fields,
            files={"file": ("x2.txt", b"Agent scoped quartz evidence.")},
        )
        assert denied_upload.status_code == 403, denied_upload.text
        denied_search = await writer.post("/v4/memory/search", json=search("agent-x"))
        assert denied_search.status_code == 403, denied_search.text

        # With the permission, the same calls work.
        allowed_write = await writer.post(
            "/v4/documents", json=document("custom-agent", "c1")
        )
        assert allowed_write.status_code == 202, allowed_write.text
        await _drain_all_work(env["dao"])
        allowed_search = await writer.post(
            "/v4/memory/search", json=search("custom-agent")
        )
        assert allowed_search.status_code == 200, allowed_search.text
        assert [item["document_id"] for item in allowed_search.json()["results"]] == ["c1"]

        # The default agent goes through the same check.
        await server.state.access_control.grant_scope_role(
            "no-agent-reader",
            tenant_id="t1",
            workspace_id="ws1",
            dataset_id="ds1",
            role="READER",
        )
        no_agent_key = await server.state.api_key_store.issue_key(
            principal_id="no-agent-reader"
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://test",
            headers={"X-API-Key": no_agent_key},
        ) as no_agent_reader:
            default_search = await no_agent_reader.post(
                "/v4/memory/search",
                json={**scope, "dataset_ids": ["ds1"], "query": "quartz evidence"},
            )
        assert default_search.status_code == 403, default_search.text
