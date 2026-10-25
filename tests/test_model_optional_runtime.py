"""P2-03: model flags, skipped projections and backfill in the real runtime."""

from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from mesa_memory.api import server
from mesa_storage.rebuild_preparation import _backlog_counts
from tests.test_d008_model_enabled_runtime_e2e import _embedding_service
from tests.test_v4_retry_lifecycle import _FlakyProvider


@pytest.fixture
def model_runtime(tmp_path, monkeypatch):
    provider = _FlakyProvider()
    for key in (
        "MESA_MODEL_ENABLED",
        "MESA_EMBEDDING_ENABLED",
        "MESA_EXTRACTION_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in {
        "MESA_RUNTIME_PROFILE": "combined",
        "MESA_STORAGE_ROOT": str(tmp_path / "storage"),
        "MESA_LOAD_DOTENV": "false",
        "MESA_EXTERNAL_PROVIDER_ENABLED": "true",
        "MESA_TIER3_MODE": "0",
        "MESA_EMBEDDING_DIMENSION": "384",
        "MESA_LLM_PROVIDER": "mock",
        "MESA_API_KEY": "model-test-key",
        "MESA_PRINCIPAL_ID": "model-principal",
        "MESA_PRINCIPAL_STATUS": "active",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        server.AdapterFactory, "get_adapter", staticmethod(lambda *a, **k: provider)
    )
    # An external service stays callable even when model loading is disabled.
    # Only the production runtime guards may prevent these provider calls.
    monkeypatch.setattr(
        server, "_get_embedding_service", lambda **kw: _embedding_service(provider)
    )

    async def hold_polling(*args, **kwargs):
        import asyncio

        await asyncio.Event().wait()

    monkeypatch.setattr(server, "_run_combined_durable_consumer", hold_polling)

    @asynccontextmanager
    async def runtime(**flags):
        for name, value in flags.items():
            monkeypatch.setenv(name, str(value).lower())
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(server.lifespan(server.app))
            dao = server.state.dao
            await dao.ensure_v4_catalog_scope(
                tenant_id="model-tenant", workspace_id="default", dataset_id="main"
            )
            access = server.state.access_control
            await access.grant_principal_permission(
                "model-principal", "model-agent", "SESSION_CREATE"
            )
            await access.grant_scope_role(
                "model-principal",
                tenant_id="model-tenant",
                workspace_id="default",
                dataset_id="main",
                role="WRITER",
            )
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=server.app),
                    base_url="http://test",
                    headers={"X-API-Key": "model-test-key"},
                )
            )
            started = await client.post(
                "/v4/sessions/start",
                json={
                    "tenant_id": "model-tenant",
                    "workspace_id": "default",
                    "dataset_ids": ["main"],
                    "agent_id": "model-agent",
                },
            )
            assert started.status_code == 201, started.text
            env = SimpleNamespace(
                dao=dao,
                provider=provider,
                client=client,
                session_id=started.json()["session_id"],
            )

            async def restart(**new_flags):
                await stack.aclose()
                for name, value in new_flags.items():
                    monkeypatch.setenv(name, str(value).lower())
                await stack.enter_async_context(server.lifespan(server.app))
                env.dao = server.state.dao
                # The ASGI client has no lifespan ownership and can be reopened.
                env.client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=server.app),
                        base_url="http://test",
                        headers={"X-API-Key": "model-test-key"},
                    )
                )

            env.restart = restart
            yield env

    return runtime


async def _insert(env):
    response = await env.client.post(
        "/v4/memory/insert",
        json={
            "session_id": env.session_id,
            "dataset_id": "main",
            "document_id": "model-doc",
            "revision_id": "model-rev",
            "chunk_id": "model-chunk",
            "title": "Durable memory",
            "source_ref": "model-test",
            "content": "MESA preserves durable memory.",
            "idempotency_key": "model-insert",
        },
    )
    assert response.status_code == 202, response.text
    env.mutation_id = response.json()["mutation_id"]


async def _consume(env, count=1):
    for _ in range(count):
        await server._consume_combined_durable_work_once(
            env.dao,
            consolidation_loop=server.state.consolidation_loop,
            model_processing_enabled=server.state.consolidation_loop is not None,
        )


async def _lanes(env):
    async with env.dao._sql.connection() as db:
        async with db.execute(
            "SELECT projection_name, state FROM projection_outbox WHERE mutation_id = ?",
            (env.mutation_id,),
        ) as cursor:
            return dict(await cursor.fetchall())


async def _search(env):
    response = await env.client.post(
        "/v4/memory/search",
        json={"session_id": env.session_id, "query": "durable memory"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["results"], response.text
    return response.json()


async def _assert_committed(env):
    mutation = await env.dao.get_mutation("model-agent", env.mutation_id)
    assert mutation["state"] == "COMMITTED"
    assert (await env.dao.get_pipeline_run(mutation["pipeline_run_id"]))[
        "state"
    ] == "COMMITTED"


@pytest.mark.asyncio
async def test_no_model_flags_store_and_search_without_provider_calls(model_runtime):
    async with model_runtime() as env:
        await _insert(env)
        await _consume(env, 3)
        await _assert_committed(env)
        assert (await _lanes(env))["VECTOR"] == "SKIPPED"
        result = await _search(env)
        assert result["vector_lane"]["status"] == "semantic_runtime_disabled"
        assert env.provider.embeddings == env.provider.completions == 0


@pytest.mark.asyncio
async def test_embedding_enabled_without_extraction_commits_vectors(model_runtime):
    async with model_runtime(
        MESA_EMBEDDING_ENABLED=True, MESA_EXTRACTION_ENABLED=False
    ) as env:
        await _insert(env)
        await _consume(env, 3)
        await _assert_committed(env)
        assert (await _lanes(env))["VECTOR"] == "COMPLETED"
        assert env.provider.embeddings > 0
        assert env.provider.completions == 0
        assert (await _search(env))["vector_lane"]["search_executed"]


@pytest.mark.asyncio
async def test_extraction_enabled_without_embedding(model_runtime):
    async with model_runtime(MESA_EXTRACTION_ENABLED=True) as env:
        await _insert(env)
        await _consume(env, 3)
        await _assert_committed(env)
        assert (await _lanes(env))["VECTOR"] == "SKIPPED"
        await _search(env)
        assert env.provider.completions == 1 and env.provider.embeddings == 0


@pytest.mark.asyncio
async def test_disabling_embedding_guards_queries_with_existing_vectors(model_runtime):
    async with model_runtime(MESA_MODEL_ENABLED=True) as env:
        await _insert(env)
        await _consume(env, 3)
        before = env.provider.embeddings
        await env.restart(MESA_EMBEDDING_ENABLED=False, MESA_EXTRACTION_ENABLED=False)
        result = await _search(env)
        assert result["vector_lane"]["status"] == "semantic_runtime_disabled"
        assert env.provider.embeddings == before


@pytest.mark.asyncio
async def test_restart_backfills_skipped_vectors_without_hiding_records(model_runtime):
    async with model_runtime() as env:
        await _insert(env)
        await _consume(env, 3)
        await env.restart(MESA_EMBEDDING_ENABLED=True)
        assert (await _lanes(env))["VECTOR"] == "PENDING"
        await _assert_committed(env)
        await _search(env)
        await _consume(env)
        assert (await _lanes(env))["VECTOR"] == "COMPLETED"
        await _assert_committed(env)
        assert (await _search(env))["vector_lane"]["search_executed"]
        assert env.provider.completions == 0


@pytest.mark.asyncio
async def test_failing_backfill_keeps_committed_record_searchable(model_runtime):
    async with model_runtime() as env:
        await _insert(env)
        await _consume(env, 3)
        await env.restart(MESA_EMBEDDING_ENABLED=True)
        env.provider.fail_embeddings = 99
        for attempt in range(1, 6):
            await _consume(env)
            await _assert_committed(env)
            assert (await _lanes(env))["VECTOR"] == (
                "DEAD_LETTER" if attempt == 5 else "RETRY_PENDING"
            )
            await _search(env)
            async with env.dao._sql.transaction() as db:
                await db.execute(
                    "UPDATE projection_outbox SET lease_expires_at = datetime('now', '-1 second') "
                    "WHERE state = 'RETRY_PENDING'"
                )
                await db.commit()
        assert env.provider.completions == 0


@pytest.mark.asyncio
async def test_legacy_model_flag_enables_both_models(model_runtime):
    async with model_runtime(MESA_MODEL_ENABLED=True) as env:
        runtime = server.state.runtime_profile
        assert getattr(runtime, "embedding_enabled", None) is True
        assert getattr(runtime, "extraction_enabled", None) is True
        await _insert(env)
        await _consume(env, 3)
        await _assert_committed(env)
        assert set((await _lanes(env)).values()) == {"COMPLETED"}
        assert env.provider.embeddings > 0 and env.provider.completions == 1


@pytest.mark.asyncio
async def test_rebuild_preparation_excludes_skipped_outbox_rows(model_runtime):
    import sqlite3

    async with model_runtime(MESA_MODEL_ENABLED=True) as env:
        await _insert(env)
        await _consume(env, 3)
        async with env.dao._sql.transaction() as db:
            await db.execute(
                "UPDATE projection_outbox SET state = 'SKIPPED' WHERE projection_name = 'VECTOR'"
            )
            await db.commit()
        with sqlite3.connect(
            server.state.runtime_profile.storage_root / "mesa.db"
        ) as db:
            assert _backlog_counts(db)["projection"] == 0
