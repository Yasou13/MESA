"""P17-01 tests: document validity validation, UTC normalization, and half-open intervals (D85, D86)."""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from mesa_client.client import AsyncMesaV4Client, MesaV4Client, MesaValidationError
from mesa_mcp.adapter import MesaMCPAdapter
from mesa_mcp.configuration import MCPSettings
from mesa_mcp.errors import MCPError
from mesa_memory.api import server
from mesa_memory.config import configured_embedding_identity
from mesa_memory.embedding.service import EmbeddingService, _deterministic_mock_vector
from mesa_storage.temporal import (
    normalize_temporal_metadata,
    normalize_utc_iso8601,
    parse_iso8601_temporal,
)


@pytest.fixture
def temporal_runtime(tmp_path, monkeypatch):
    """Real combined runtime with configurable embedding (models-off or deterministic)."""

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
            "MESA_API_KEY": "temporal-test-key",
            "MESA_PRINCIPAL_ID": "temporal-principal",
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
            import asyncio

            await asyncio.Event().wait()

        monkeypatch.setattr(server, "_run_combined_durable_consumer", hold_polling)

        async with AsyncExitStack() as stack:
            await stack.enter_async_context(server.lifespan(server.app))
            dao = server.state.dao
            await dao.ensure_v4_catalog_scope(
                tenant_id="temp-tenant", workspace_id="default", dataset_id="laws"
            )
            access = server.state.access_control
            await access.grant_principal_permission(
                "temporal-principal", "temporal-agent", "SESSION_CREATE"
            )
            await access.grant_scope_role(
                "temporal-principal",
                tenant_id="temp-tenant",
                workspace_id="default",
                dataset_id="laws",
                role="WRITER",
            )
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=server.app),
                    base_url="http://test",
                    headers={"X-API-Key": "temporal-test-key"},
                )
            )
            started = await client.post(
                "/v4/sessions/start",
                json={
                    "tenant_id": "temp-tenant",
                    "workspace_id": "default",
                    "dataset_ids": ["laws"],
                    "agent_id": "temporal-agent",
                },
            )
            assert started.status_code == 201, started.text
            yield SimpleNamespace(
                dao=dao,
                client=client,
                session_id=started.json()["session_id"],
            )

    return runtime


async def _drain_runtime(env, max_steps: int = 30) -> None:
    for _ in range(max_steps):
        work = await server._consume_combined_durable_work_once(
            env.dao,
            fact_extraction_service=server.state.fact_extraction_service,
            model_processing_enabled=server.state.fact_extraction_service is not None,
        )
        if not work:
            break


def _law_text(article1_body: str, article2_body: str, article3_body: str) -> str:
    return (
        "DENEME CEZA KANUNU\n"
        "Kanun Numarası: 5237\n\n"
        f"MADDE 1 - {article1_body}\n\n"
        f"MADDE 2 - {article2_body}\n\n"
        f"MADDE 3 - {article3_body}\n"
    )


def test_shared_temporal_helper_normalizes_utc_and_rejects_invalid() -> None:
    assert normalize_utc_iso8601("2024-01-01T03:00:00+03:00") == "2024-01-01T00:00:00Z"
    assert normalize_utc_iso8601("2024-01-01T00:00:00z") == "2024-01-01T00:00:00Z"
    assert normalize_temporal_metadata(
        {
            "valid_from": "2020-01-01T03:00:00+03:00",
            "valid_to": "2024-01-01T03:00:00+03:00",
            "extra": "kept",
        }
    ) == {
        "valid_from": "2020-01-01T00:00:00Z",
        "valid_to": "2024-01-01T00:00:00Z",
        "extra": "kept",
    }

    for bad in (
        "2024-01-01",
        "2024-01-01T00:00:00",
        "2024-01-01Z",
        "",
        "   ",
        "not-a-date",
        1704067200,
        True,
    ):
        with pytest.raises(ValueError):
            parse_iso8601_temporal(bad)

    with pytest.raises(ValueError, match="valid_from must be before valid_to"):
        normalize_temporal_metadata(
            {
                "valid_from": "2024-01-01T00:00:00Z",
                "valid_to": "2024-01-01T00:00:00Z",
            }
        )
    with pytest.raises(ValueError, match="valid_from must be before valid_to"):
        normalize_temporal_metadata(
            {
                "valid_from": "2024-01-01T04:00:00+03:00",
                "valid_to": "2024-01-01T00:00:00Z",
            }
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_metadata",
    [
        {"valid_from": "2024-01-01"},
        {"valid_from": "2024-01-01T00:00:00"},
        {"valid_from": "not-a-date"},
        {"valid_from": ""},
        {"valid_from": 1704067200},
        {"valid_to": "2024-12-31"},
        {"valid_from": "2024-01-01T00:00:00Z", "valid_to": "2024-01-01T00:00:00Z"},
        {"valid_from": "2025-01-01T00:00:00Z", "valid_to": "2024-01-01T00:00:00Z"},
    ],
)
async def test_document_ingest_rejects_invalid_dates_with_422_and_writes_nothing(
    temporal_runtime, bad_metadata
) -> None:
    async with temporal_runtime() as env:
        resp = await env.client.post(
            "/v4/documents",
            json={
                "session_id": env.session_id,
                "dataset_id": "laws",
                "document_id": "doc-bad-date",
                "title": "Bad Date Law",
                "source_ref": "law://bad",
                "content": "Madde 1 - Gecersiz tarih testi.",
                "metadata": bad_metadata,
            },
        )
        assert resp.status_code == 422, resp.text

        async with env.dao._sql.connection() as db:
            for table in (
                "documents",
                "document_revisions",
                "source_chunks",
                "memory_mutations",
            ):
                async with db.execute(f"SELECT COUNT(*) FROM {table}") as cur:
                    assert int((await cur.fetchone())[0]) == 0, f"{table} was mutated on 422"


@pytest.mark.asyncio
async def test_sdk_and_mcp_validate_and_utc_normalize_metadata() -> None:
    # Sync SDK rejects invalid dates before any network call
    sync_client = MesaV4Client(base_url="http://test", api_key="k")
    try:
        with pytest.raises(MesaValidationError):
            sync_client.add_document(
                session_id="s1",
                dataset_id="laws",
                document_id="doc-1",
                title="T",
                source_ref="src",
                content="text",
                metadata={"valid_from": "2024-01-01"},
            )
    finally:
        sync_client.close()

    # Async SDK normalizes non-UTC offset to UTC before sending
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            202,
            json={
                "document_id": "doc-1",
                "revision_id": "rev-1",
                "revision_number": 1,
                "chunk_count": 1,
                "mutation_ids": ["m1"],
                "duplicate": False,
            },
        )

    async_client = AsyncMesaV4Client(base_url="http://test", api_key="k")
    async_client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    try:
        await async_client.add_document(
            session_id="s1",
            dataset_id="laws",
            document_id="doc-1",
            title="T",
            source_ref="src",
            content="text",
            metadata={
                "valid_from": "2024-01-01T03:00:00+03:00",
                "valid_to": "2025-01-01T03:00:00+03:00",
            },
        )
        assert captured[0]["metadata"] == {
            "valid_from": "2024-01-01T00:00:00Z",
            "valid_to": "2025-01-01T00:00:00Z",
        }
        with pytest.raises(MesaValidationError):
            await async_client.add_document(
                session_id="s1",
                dataset_id="laws",
                document_id="doc-1",
                title="T",
                source_ref="src",
                content="text",
                metadata={
                    "valid_from": "2024-01-01T00:00:00Z",
                    "valid_to": "2024-01-01T00:00:00Z",
                },
            )
    finally:
        await async_client.aclose()

    # MCP rejects invalid dates and UTC-normalizes valid ones
    v4 = AsyncMock()
    v4.v4_remember = AsyncMock(return_value={"status": "accepted"})
    v4.v4_improve = AsyncMock(return_value={"status": "accepted"})
    adapter = MesaMCPAdapter(AsyncMock(), MCPSettings(), v4)

    with pytest.raises(MCPError) as exc_info:
        await adapter.mesa_remember(
            {"content": "text", "metadata": {"valid_from": "2024-01-01"}}
        )
    assert exc_info.value.code == "INVALID_ARGUMENT"
    v4.v4_remember.assert_not_awaited()

    await adapter.mesa_remember(
        {
            "content": "text",
            "metadata": {"valid_from": "2024-06-01T03:00:00+03:00"},
        }
    )
    assert v4.v4_remember.await_args.kwargs["metadata"] == {
        "valid_from": "2024-06-01T00:00:00Z"
    }

    with pytest.raises(MCPError) as exc_info2:
        await adapter.mesa_improve(
            {
                "document_id": "doc-1",
                "content": "text",
                "metadata": {
                    "valid_from": "2025-01-01T00:00:00Z",
                    "valid_to": "2024-01-01T00:00:00Z",
                },
            }
        )
    assert exc_info2.value.code == "INVALID_ARGUMENT"
    v4.v4_improve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("embedding_enabled", [False, True], ids=["models_off", "deterministic_vectors"])
async def test_half_open_validity_across_lanes_and_vectors(
    temporal_runtime, embedding_enabled: bool
) -> None:
    """Two dated revisions: verify undated, before, exact boundary, after, and ranges."""
    async with temporal_runtime(
        embedding_enabled=embedding_enabled,
        storage_name=f"storage_{'vec' if embedding_enabled else 'bm25'}",
    ) as env:
        v1_text = _law_text(
            "Eski ceza oranı binde on olarak uygulanır.",
            "Eski yürürlük hükmü iki yıl geçerlidir.",
            "Genel kapsam hükmü sabit kalır.",
        )
        v2_text = _law_text(
            "Yeni ceza oranı binde yirmi olarak uygulanır.",
            "Yeni yürürlük hükmü beş yıl geçerlidir.",
            "Genel kapsam hükmü sabit kalır.",
        )

        # Ingest Rev 1 with +03:00 offset -> normalized to 2020-01-01T00:00:00Z
        r1 = await env.client.post(
            "/v4/documents",
            json={
                "session_id": env.session_id,
                "dataset_id": "laws",
                "document_id": "tck-5237",
                "title": "Türk Ceza Kanunu",
                "source_ref": "mevzuat://5237/v1",
                "content": v1_text,
                "metadata": {"valid_from": "2020-01-01T03:00:00+03:00"},
            },
        )
        assert r1.status_code == 202, r1.text
        rev1_id = r1.json()["revision_id"]
        await _drain_runtime(env)

        # Ingest Rev 2 with +03:00 offset -> normalized to 2024-01-01T00:00:00Z
        r2 = await env.client.post(
            "/v4/documents",
            json={
                "session_id": env.session_id,
                "dataset_id": "laws",
                "document_id": "tck-5237",
                "title": "Türk Ceza Kanunu",
                "source_ref": "mevzuat://5237/v2",
                "content": v2_text,
                "metadata": {"valid_from": "2024-01-01T03:00:00+03:00"},
            },
        )
        assert r2.status_code == 202, r2.text
        rev2_id = r2.json()["revision_id"]
        await _drain_runtime(env)

        # Verify UTC-normalized validity bounds in canonical SQLite assertions
        async with env.dao._sql.connection() as db:
            async with db.execute(
                "SELECT DISTINCT a.status, a.valid_from, a.valid_to, ci.external_id "
                "FROM v4_assertions a "
                "JOIN v4_catalog_identities ci "
                "ON ci.kind = 'revision' AND ci.physical_id = a.revision_id "
                "ORDER BY ci.external_id"
            ) as cur:
                rows = {r[3]: (r[0], r[1], r[2]) for r in await cur.fetchall()}
        assert rows[rev1_id] == (
            "SUPERSEDED",
            "2020-01-01T00:00:00Z",
            "2024-01-01T00:00:00Z",
        )
        assert rows[rev2_id] == ("ACTIVE", "2024-01-01T00:00:00Z", "")

        async def _search(query: str, **temporal):
            resp = await env.client.post(
                "/v4/memory/search",
                json={
                    "session_id": env.session_id,
                    "query": query,
                    "limit": 10,
                    **temporal,
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            revs = {
                item["provenance"][0]["revision_id"] for item in body["results"]
            }
            return body, revs

        # Test both a lexical/vector query AND an exact legal citation query ("TCK m. 1")
        for query in ("ceza oranı binde uygulanır", "TCK m. 1 ceza oranı"):
            # 1. Undated search -> ACTIVE revision only (Rev 2)
            body_undated, revs_undated = await _search(query)
            assert revs_undated == {rev2_id}
            if embedding_enabled:
                assert body_undated["vector_lane"]["status"] == "active"
                assert any(
                    "vector" in item["retrieval_provenance"]["origins"]
                    for item in body_undated["results"]
                )

            # 2. Before Rev 1 -> empty
            _, revs_before_all = await _search(
                query, valid_at="2019-12-31T23:59:59Z"
            )
            assert revs_before_all == set()

            # 3. Exact start of Rev 1 -> Rev 1 only
            body_start, revs_start = await _search(
                query, valid_at="2020-01-01T00:00:00Z"
            )
            assert revs_start == {rev1_id}
            if embedding_enabled:
                assert body_start["vector_lane"]["status"] == "active"

            # 4. Mid Rev 1 -> Rev 1 only
            _, revs_mid = await _search(query, valid_at="2022-06-15T12:00:00Z")
            assert revs_mid == {rev1_id}

            # 5. Exact amendment instant (UTC and +03:00) -> ONLY Rev 2 (D86 half-open!)
            body_boundary, revs_boundary = await _search(
                query, valid_at="2024-01-01T00:00:00Z"
            )
            assert revs_boundary == {rev2_id}
            if embedding_enabled:
                assert body_boundary["vector_lane"]["status"] == "active"

            _, revs_boundary_tz = await _search(
                query, valid_at="2024-01-01T03:00:00+03:00"
            )
            assert revs_boundary_tz == {rev2_id}

            # 6. After amendment -> ONLY Rev 2
            _, revs_after = await _search(query, valid_at="2025-06-01T00:00:00Z")
            assert revs_after == {rev2_id}

            # 7. Range filters with half-open overlap (D86)
            _, revs_from_boundary = await _search(
                query, valid_from="2024-01-01T00:00:00Z"
            )
            assert revs_from_boundary == {rev2_id}

            _, revs_to_boundary = await _search(
                query, valid_to="2024-01-01T00:00:00Z"
            )
            assert revs_to_boundary == {rev1_id}

            _, revs_spanning = await _search(
                query,
                valid_from="2022-01-01T00:00:00Z",
                valid_to="2025-01-01T00:00:00Z",
            )
            assert revs_spanning == {rev1_id, rev2_id}
