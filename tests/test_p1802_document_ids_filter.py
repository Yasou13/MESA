"""P18-02 tests: document_ids search filter across all lanes, cache, SDK and MCP (D95)."""

from __future__ import annotations

import asyncio
import json
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
from mesa_memory.config import configured_embedding_identity
from mesa_memory.embedding.service import EmbeddingService, _deterministic_mock_vector


async def _drain_work(dao, max_steps: int = 40) -> None:
    for _ in range(max_steps):
        work = await server._consume_combined_durable_work_once(
            dao,
            fact_extraction_service=server.state.fact_extraction_service,
            model_processing_enabled=server.state.fact_extraction_service is not None,
        )
        if not work:
            break


@pytest.fixture
def d95_runtime(tmp_path, monkeypatch):
    @asynccontextmanager
    async def runtime(*, embedding_enabled: bool = False, storage_name: str = "storage"):
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
            "MESA_STORAGE_ROOT": str(tmp_path / storage_name),
            "MESA_LOAD_DOTENV": "false",
            "MESA_EXTERNAL_PROVIDER_ENABLED": "false",
            "MESA_MODEL_ENABLED": "false",
            "MESA_EMBEDDING_ENABLED": "true" if embedding_enabled else "false",
            "MESA_EXTRACTION_ENABLED": "false",
            "MESA_API_KEY": "d95-test-key",
            "MESA_PRINCIPAL_ID": "d95-principal",
            "MESA_PRINCIPAL_STATUS": "active",
        }
        if embedding_enabled:
            env_vars["MESA_EMBEDDING_DIMENSION"] = "64"
            monkeypatch.setattr(
                server,
                "_get_embedding_service",
                lambda **_kw: EmbeddingService(
                    identity=configured_embedding_identity(),
                    provider_fn=lambda text: _deterministic_mock_vector(text, 64),
                    allow_model_loading=False,
                    external_enabled=False,
                ),
            )
        for key, value in env_vars.items():
            monkeypatch.setenv(key, value)

        async def hold_polling(*args, **kwargs):
            await asyncio.Event().wait()

        monkeypatch.setattr(server, "_run_combined_durable_consumer", hold_polling)

        async with AsyncExitStack() as stack:
            await stack.enter_async_context(server.lifespan(server.app))
            dao = server.state.dao
            access = server.state.access_control
            await dao.ensure_v4_catalog_scope(
                tenant_id="t1", workspace_id="ws1", dataset_id="ds1"
            )
            await access.grant_scope_role(
                "d95-principal",
                tenant_id="t1",
                workspace_id="ws1",
                dataset_id="ds1",
                role="WRITER",
            )
            # R25 amendment A1: sessionless calls need the agent permission.
            await access.grant_principal_permission(
                "d95-principal", "local-agent", "SESSION_CREATE"
            )
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=server.app),
                    base_url="http://test",
                    headers={"X-API-Key": "d95-test-key"},
                )
            )
            yield dao, client

    return runtime


def _law_text(law_no: str, title: str, body1: str, body2: str, body3: str) -> str:
    return (
        f"{title}\n"
        f"Kanun Numarası: {law_no}\n\n"
        f"MADDE 1 - {body1}\n\n"
        f"MADDE 2 - {body2}\n\n"
        f"MADDE 3 - {body3}\n"
    )


@pytest.mark.asyncio
async def test_document_ids_filter_bm25_legal_unknown_and_cache(d95_runtime) -> None:
    async with d95_runtime(embedding_enabled=False) as (dao, client):
        for doc_id, text in (
            (
                "law-5237",
                _law_text(
                    "5237",
                    "DENEME CEZA KANUNU",
                    "Kira bedeli ceza yaptırımı ve tazminat kuralları burada yer alır.",
                    "İkinci madde ortak kelime olarak kira sözleşmesi içerir.",
                    "Üçüncü madde yürürlük hükümlerini düzenler.",
                ),
            ),
            (
                "law-6098",
                _law_text(
                    "6098",
                    "DENEME BORÇLAR KANUNU",
                    "Kira bedeli borçlar hukuku uyarınca ayın on beşinde ödenir.",
                    "İkinci madde ortak kelime olarak kira sözleşmesi içerir.",
                    "Üçüncü madde genel hükümleri düzenler.",
                ),
            ),
        ):
            r = await client.post(
                "/v4/documents",
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_id": "ds1",
                    "document_id": doc_id,
                    "title": doc_id,
                    "source_ref": f"file://{doc_id}.txt",
                    "content": text,
                },
            )
            assert r.status_code == 202, r.text
        await _drain_work(dao)

        # Unfiltered search returns both documents and uses persistent FTS.
        unfiltered = await client.post(
            "/v4/memory/search",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_ids": ["ds1"],
                "query": "kira sözleşmesi",
                "limit": 10,
            },
        )
        assert unfiltered.status_code == 200, unfiltered.text
        unfiltered_docs = {item["document_id"] for item in unfiltered.json()["results"]}
        assert unfiltered_docs == {"law-5237", "law-6098"}
        unfiltered_scope = next(
            s for k, s in dao._search_scope_cache.items() if k[-1] is None
        )
        assert unfiltered_scope["exact_dataset_scope"] is True

        # Filtered BM25 search returns only law-6098 and never uses persistent FTS.
        only_6098 = await client.post(
            "/v4/memory/search",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_ids": ["ds1"],
                "document_ids": ["law-6098"],
                "query": "kira sözleşmesi",
                "limit": 10,
            },
        )
        assert only_6098.status_code == 200, only_6098.text
        docs_6098 = {item["document_id"] for item in only_6098.json()["results"]}
        assert docs_6098 == {"law-6098"}
        filtered_scope = next(
            s for k, s in dao._search_scope_cache.items() if k[-1] == ("law-6098",)
        )
        assert filtered_scope["exact_dataset_scope"] is False

        # Legal exact-match lane must respect document_ids filter:
        # citing 5237 m. 1 while restricting to law-6098 must never return law-5237.
        legal_filtered = await client.post(
            "/v4/memory/search",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_ids": ["ds1"],
                "document_ids": ["law-6098"],
                "query": "5237 sayılı Kanun m. 1 kira bedeli",
                "limit": 10,
            },
        )
        assert legal_filtered.status_code == 200, legal_filtered.text
        assert all(
            item["document_id"] == "law-6098"
            for item in legal_filtered.json()["results"]
        )

        # Citing 5237 m. 1 restricted to law-5237 returns law-5237 via exact match.
        legal_match = await client.post(
            "/v4/memory/search",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_ids": ["ds1"],
                "document_ids": ["law-5237"],
                "query": "5237 sayılı Kanun m. 1",
                "limit": 10,
            },
        )
        assert legal_match.status_code == 200, legal_match.text
        match_results = legal_match.json()["results"]
        assert match_results and all(
            item["document_id"] == "law-5237" for item in match_results
        )
        assert "exact_match" in match_results[0]["retrieval_provenance"]

        # Unknown document id returns empty results (200), not an error.
        unknown = await client.post(
            "/v4/memory/search",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_ids": ["ds1"],
                "document_ids": ["does-not-exist"],
                "query": "kira bedeli",
            },
        )
        assert unknown.status_code == 200, unknown.text
        assert unknown.json()["results"] == []

        # Cache does not leak back to unfiltered search.
        unfiltered_again = await client.post(
            "/v4/memory/search",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_ids": ["ds1"],
                "query": "kira sözleşmesi",
                "limit": 10,
            },
        )
        assert unfiltered_again.status_code == 200
        assert {
            item["document_id"] for item in unfiltered_again.json()["results"]
        } == {"law-5237", "law-6098"}

        # Validation: empty list or >64 IDs rejected with 422.
        empty_resp = await client.post(
            "/v4/memory/search",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_ids": ["ds1"],
                "document_ids": [],
                "query": "kira",
            },
        )
        assert empty_resp.status_code == 422
        too_many = await client.post(
            "/v4/memory/search",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_ids": ["ds1"],
                "document_ids": [f"doc-{i}" for i in range(65)],
                "query": "kira",
            },
        )
        assert too_many.status_code == 422


@pytest.mark.asyncio
async def test_document_ids_filter_vector_lane(d95_runtime) -> None:
    async with d95_runtime(embedding_enabled=True, storage_name="vec_storage") as (
        dao,
        client,
    ):
        for doc_id, content in (
            ("vec-alpha", "Alpha vector evidence on maritime cargo liability."),
            ("vec-beta", "Beta vector evidence on maritime cargo liability."),
        ):
            r = await client.post(
                "/v4/documents",
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_id": "ds1",
                    "document_id": doc_id,
                    "title": doc_id,
                    "source_ref": f"file://{doc_id}.txt",
                    "content": content,
                },
            )
            assert r.status_code == 202, r.text
        await _drain_work(dao)

        filtered = await client.post(
            "/v4/memory/search",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_ids": ["ds1"],
                "document_ids": ["vec-beta"],
                "query": "maritime cargo liability",
                "limit": 10,
            },
        )
        assert filtered.status_code == 200, filtered.text
        body = filtered.json()
        assert body["vector_lane"]["search_executed"] is True
        assert body["vector_lane"]["allowed_vector_id_count"] >= 1
        assert body["results"]
        assert {item["document_id"] for item in body["results"]} == {"vec-beta"}


@pytest.mark.asyncio
async def test_document_ids_sdk_and_mcp(d95_runtime) -> None:
    async with d95_runtime(embedding_enabled=False, storage_name="sdk_mcp") as (
        dao,
        client,
    ):
        for doc_id, content in (
            ("doc-one", "Shared keyword aurora in document one."),
            ("doc-two", "Shared keyword aurora in document two."),
        ):
            await client.post(
                "/v4/documents",
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_id": "ds1",
                    "document_id": doc_id,
                    "title": doc_id,
                    "source_ref": f"file://{doc_id}.txt",
                    "content": content,
                },
            )
        await _drain_work(dao)

        # Async SDK
        async_sdk = AsyncMesaV4Client(base_url="http://test", api_key="d95-test-key")
        async_sdk._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://test",
            headers={"X-API-Key": "d95-test-key"},
        )
        try:
            resp = await async_sdk.search(
                tenant_id="t1",
                workspace_id="ws1",
                dataset_ids=["ds1"],
                document_ids=["doc-two"],
                query="Shared keyword aurora",
            )
            assert {item["document_id"] for item in resp["results"]} == {"doc-two"}
        finally:
            await async_sdk.aclose()

        # MCP
        settings = MCPSettings(
            base_url="http://test",
            api_key="d95-test-key",
            default_tenant_id="t1",
            default_workspace_id="ws1",
            default_dataset_id="ds1",
            actor_id="local-agent",
        )
        v4_svc = MesaHttpV4Service(settings)
        v4_svc._http_client._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://test",
            headers={"X-API-Key": "d95-test-key"},
        )
        adapter = MesaMCPAdapter(AsyncMock(), settings, v4_svc)
        try:
            mcp_res = await adapter.mesa_recall(
                {
                    "query": "Shared keyword aurora",
                    "dataset_ids": ["ds1"],
                    "document_ids": ["doc-one"],
                }
            )
            assert mcp_res["total"] == 1
            assert mcp_res["results"][0]["document_id"] == "doc-one"

            with pytest.raises(MCPError, match="document_ids"):
                await adapter.mesa_recall(
                    {"query": "aurora", "document_ids": []}
                )
        finally:
            await v4_svc.close()

    # Sync SDK payload check
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200, json={"session_id": None, "dataset_ids": ["ds1"], "results": []}
        )

    sync_sdk = MesaV4Client(base_url="http://test", api_key="k")
    sync_sdk._client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    try:
        sync_sdk.search(
            tenant_id="t1",
            workspace_id="ws1",
            dataset_ids=["ds1"],
            document_ids=["doc-one"],
            query="aurora",
        )
    finally:
        sync_sdk.close()
    assert seen[0]["document_ids"] == ["doc-one"]
