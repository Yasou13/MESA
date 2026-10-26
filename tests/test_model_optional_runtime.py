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
        "MESA_LLM_MODEL": "mock:test",
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
            env.stop = stack.aclose
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


@pytest.mark.asyncio
async def test_identity_change_rebuild_allows_skipped_vector_backfill(
    tmp_path, monkeypatch, model_runtime
):
    import argparse
    import hashlib

    from mesa_memory import rebuild_runner
    from mesa_storage.repositories.operations import OperationRepository
    from mesa_storage.sqlite_engine import AsyncEngine

    async with model_runtime(
        MESA_EMBEDDING_VERSION="v1", MESA_V4_REBUILD_ENABLED=True
    ) as env:
        await _insert(env)
        await _consume(env, 3)
        await _assert_committed(env)
        assert (await _lanes(env))["VECTOR"] == "SKIPPED"

        storage_root = server.state.runtime_profile.storage_root
        # A vector-free generation now adopts the new identity without a rebuild.
        await env.restart(MESA_EMBEDDING_ENABLED=False, MESA_EMBEDDING_VERSION="v2")
        assert (await _lanes(env))["VECTOR"] == "SKIPPED"
        await _assert_committed(env)

        await env.stop()  # Offline rebuild needs the combined writer lock released.
        sql = AsyncEngine(str(storage_root / "mesa.db"))
        await sql.initialize()
        try:
            operations = OperationRepository(sql)
            submitted = await operations.submit(
                requested_by_principal_id="model-principal",
                idempotency_key="r6-identity-rebuild-backfill",
                payload_hash=hashlib.sha256(b"r6-identity-rebuild").hexdigest(),
            )
            operation_id = str(submitted["operation_id"])
        finally:
            await sql.close()

        work_root = tmp_path / "rebuild-work"
        work_root.mkdir()
        monkeypatch.setattr(
            rebuild_runner,
            "get_embedding_service",
            lambda **kw: _embedding_service(env.provider),
        )
        rc = await rebuild_runner.run_rebuild(
            argparse.Namespace(
                trusted_root=tmp_path,
                storage_root=storage_root,
                work_root=work_root,
                operation_id=operation_id,
                batch_size=100,
                lease_seconds=300,
            )
        )
        assert rc == rebuild_runner.EXIT_OK

        await env.restart(MESA_EMBEDDING_ENABLED=True, MESA_EMBEDDING_VERSION="v2")
        assert (await _lanes(env))["VECTOR"] == "PENDING"
        await _consume(env)
        assert (await _lanes(env))["VECTOR"] == "COMPLETED"
        await _assert_committed(env)
        result = await _search(env)
        assert result["vector_lane"]["search_executed"] is True
        assert result["degraded_sources"] == []

        vector_ids = await env.dao._vec.get_active_node_ids("model-agent")
        assert len(vector_ids) == 1
        await env.dao._vec.hard_delete(sorted(vector_ids)[0], "model-agent")
        repaired = await env.dao.reconcile_v4_projection_parity(repair=True)
        assert repaired["missing_artifacts"] == 1
        assert repaired["requeued_lanes"] == 1
        await _consume(env)
        assert (await env.dao.reconcile_v4_projection_parity())["missing_artifacts"] == 0
        await _assert_committed(env)


@pytest.mark.asyncio
async def test_external_openai_compatible_provider_makes_zero_embedding_calls_when_disabled(
    tmp_path, monkeypatch
):
    from fastapi import FastAPI

    from mesa_memory.config import config
    from mesa_memory.embedding import service as embedding_service_mod

    original_config = {
        field: getattr(config, field)
        for field in (
            "external_provider_enabled",
            "embedding_model",
            "embedding_dimension",
            "embedding_version",
            "embedding_model_revision",
            "embedding_normalized",
        )
    }
    calls: list[tuple[str, str]] = []

    class CountingExternalTransport:
        def __init__(self, identity) -> None:
            self.identity = identity

        def embed_document(self, text: str) -> list[float]:
            calls.append(("document", text))
            return [1.0, 0.0, 0.0, 0.0]

        async def aembed_document(self, text: str) -> list[float]:
            calls.append(("document", text))
            return [1.0, 0.0, 0.0, 0.0]

        def embed_query(self, text: str) -> list[float]:
            calls.append(("query", text))
            return [1.0, 0.0, 0.0, 0.0]

        async def aembed_query(self, text: str) -> list[float]:
            calls.append(("query", text))
            return [1.0, 0.0, 0.0, 0.0]

    for key, value in {
        "MESA_RUNTIME_PROFILE": "combined",
        "MESA_STORAGE_ROOT": str(tmp_path / "external-disabled-storage"),
        "MESA_LOAD_DOTENV": "false",
        "MESA_MODEL_ENABLED": "false",
        "MESA_EMBEDDING_ENABLED": "false",
        "MESA_EXTRACTION_ENABLED": "false",
        "MESA_EXTERNAL_PROVIDER_ENABLED": "true",
        "MESA_EMBEDDING_MODEL": "openai_compatible:external-test-model",
        "MESA_EMBEDDING_DIMENSION": "4",
        "MESA_EMBEDDING_VERSION": "v1",
        "MESA_EMBEDDING_NORMALIZED": "true",
        "MESA_LLM_API_KEY": "test-only-key",
        "MESA_EMBEDDING_API_KEY": "test-only-key",
        "MESA_API_KEY": "model-test-key",
        "MESA_PRINCIPAL_ID": "model-principal",
        "MESA_PRINCIPAL_STATUS": "active",
    }.items():
        monkeypatch.setenv(key, value)

    monkeypatch.setattr(
        embedding_service_mod,
        "_OpenAICompatibleEmbeddingBackend",
        CountingExternalTransport,
    )

    async def hold_polling(*args, **kwargs):
        import asyncio

        await asyncio.Event().wait()

    monkeypatch.setattr(server, "_run_combined_durable_consumer", hold_polling)

    try:
        async with server.lifespan(FastAPI()):
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
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server.app),
                base_url="http://test",
                headers={"X-API-Key": "model-test-key"},
            ) as client:
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
                    client=client,
                    session_id=started.json()["session_id"],
                )
                await _insert(env)
                await _consume(env, 3)
                await _assert_committed(env)
                assert (await _lanes(env))["VECTOR"] == "SKIPPED"
                result = await _search(env)
                assert result["vector_lane"]["status"] == "semantic_runtime_disabled"
                assert result["degraded_sources"] == ["vector"]
                assert calls == []
    finally:
        embedding_service_mod.set_global_embedding_service(None)
        for field, value in original_config.items():
            object.__setattr__(config, field, value)


@pytest.mark.asyncio
async def test_backfill_keeps_identity_of_skipped_mutation_with_vectors(model_runtime):
    async with model_runtime(MESA_EMBEDDING_ENABLED=True) as env:
        await _insert(env)
        await _consume(env, 3)
        await _assert_committed(env)
        # A committed record with vectors reaches a SKIPPED VECTOR lane: parity
        # repair requeues the lane, then the runtime restarts with embedding off.
        vector_ids = await env.dao._vec.get_active_node_ids("model-agent")
        await env.dao._vec.hard_delete(sorted(vector_ids)[0], "model-agent")
        assert (await env.dao.reconcile_v4_projection_parity(repair=True))[
            "requeued_lanes"
        ] == 1
        await env.restart(MESA_EMBEDDING_ENABLED=False)
        await _consume(env)
        assert (await _lanes(env))["VECTOR"] == "SKIPPED"
        await _assert_committed(env)

        async def identity():
            async with env.dao._sql.connection() as db:
                async with db.execute(
                    "SELECT embedding_version, metadata_json FROM memory_mutations "
                    "WHERE mutation_id = ?",
                    (env.mutation_id,),
                ) as cursor:
                    return tuple(await cursor.fetchone())

        async with env.dao._sql.transaction() as db:
            await db.execute(
                "UPDATE memory_mutations SET embedding_version = 'v0' "
                "WHERE mutation_id = ?",
                (env.mutation_id,),
            )
            await db.commit()
        before = await identity()

        await env.restart(MESA_EMBEDDING_ENABLED=True)
        assert await identity() == before
        assert (await _lanes(env))["VECTOR"] == "PENDING"
        await _consume(env)
        # The durable identity still differs, so the lane fails closed.
        assert (await _lanes(env))["VECTOR"] == "RETRY_PENDING"
        await _assert_committed(env)
        assert (await _search(env))["degraded_sources"] == ["vector"]
