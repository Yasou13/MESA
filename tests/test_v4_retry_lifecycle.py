"""Retry and crash redelivery through the real combined runtime (F-01)."""

import asyncio
import json
import sqlite3
from contextlib import AsyncExitStack
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from mesa_memory.api import server
from mesa_memory.config import configured_embedding_identity
from mesa_storage import dao as dao_module
from tests.test_d008_model_enabled_runtime_e2e import (
    _DeterministicProvider,
    _embedding_service,
    _wait_for_committed,
)


class _FlakyProvider(_DeterministicProvider):
    def __init__(self) -> None:
        super().__init__()
        self.fail_first = 0
        self.schema_calls = 0
        self.fail_embeddings = 0

    def complete(self, prompt: str, schema: Any = None, **kwargs: Any) -> Any:
        if schema is not None:
            self.schema_calls += 1
            if self.schema_calls <= self.fail_first:
                raise ConnectionError("temporary provider outage")
        return super().complete(prompt, schema, **kwargs)

    def embed(self, text: str, **kwargs: Any) -> list[float]:
        if self.fail_embeddings:
            self.fail_embeddings -= 1
            raise ConnectionError("temporary embedding outage")
        return super().embed(text, **kwargs)


@pytest.fixture
def immediate_retries(monkeypatch):
    monkeypatch.setattr(dao_module, "_retry_delay_seconds", lambda _attempt: 0)


@pytest.fixture
async def retry_runtime(tmp_path, monkeypatch):
    provider = _FlakyProvider()
    for key, value in {
        "MESA_RUNTIME_PROFILE": "combined",
        "MESA_STORAGE_ROOT": str(tmp_path / "storage"),
        "MESA_LOAD_DOTENV": "false",
        "MESA_MODEL_ENABLED": "true",
        "MESA_EXTERNAL_PROVIDER_ENABLED": "true",
        "MESA_TIER3_MODE": "0",
        "MESA_EMBEDDING_DIMENSION": "384",
        "MESA_LLM_PROVIDER": "mock",
        "MESA_API_KEY": "retry-test-key",
        "MESA_PRINCIPAL_ID": "retry-principal",
        "MESA_PRINCIPAL_STATUS": "active",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        server.AdapterFactory, "get_adapter", staticmethod(lambda *a, **k: provider)
    )
    monkeypatch.setattr(
        server, "_get_embedding_service", lambda **kw: _embedding_service(provider)
    )
    # Hold only polling until crash-window setup is durable. The real consumer,
    # validation, extraction, stores, projection and HTTP authorization all run.
    start = asyncio.Event()
    consume = server._run_combined_durable_consumer

    async def after_setup(*args, **kwargs):
        await start.wait()
        await consume(*args, **kwargs)

    monkeypatch.setattr(server, "_run_combined_durable_consumer", after_setup)
    async with AsyncExitStack() as runtime:
        await runtime.enter_async_context(server.lifespan(server.app))
        dao = server.state.dao
        await dao.ensure_v4_catalog_scope(
            tenant_id="retry-tenant", workspace_id="default", dataset_id="main"
        )
        session = await dao.create_v4_session(
            tenant_id="retry-tenant",
            workspace_id="default",
            dataset_ids=["main"],
            agent_id="retry-agent",
            principal_id="retry-principal",
        )
        access = server.state.access_control
        await access.grant_access("retry-agent", session["session_id"], "READ")
        await access.grant_principal_session_access(
            "retry-principal", "retry-agent", session["session_id"], "READ"
        )
        await access.grant_scope_role(
            "retry-principal",
            tenant_id="retry-tenant",
            workspace_id="default",
            dataset_id="main",
            role="READER",
        )
        identity = configured_embedding_identity()
        admitted = await dao.admit_v4_memory(
            tenant_id="retry-tenant",
            workspace_id="default",
            dataset_id="main",
            agent_id="retry-agent",
            session_id=session["session_id"],
            document_id="retry-doc",
            revision_id="retry-rev",
            chunk_id="retry-chunk",
            title="Retry lifecycle",
            content_payload="MESA preserves durable memory.",
            source_ref="retry-test",
            evidence_span="0:30",
            revision_number=1,
            chunk_ordinal=0,
            supersedes_revision_id=None,
            metadata={},
            embedding_provider=identity.provider,
            embedding_model=identity.model,
            embedding_version=identity.version,
            embedding_dimension=identity.dimension,
            validation_mode=0,
            policy=server.config.queue_admission_policy,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://test",
            headers={"X-API-Key": "retry-test-key"},
        ) as client:
            env = SimpleNamespace(
                dao=dao,
                provider=provider,
                start=start,
                client=client,
                mutation_id=admitted["response"]["mutation_id"],
                raw_log_id=admitted["response"]["raw_log_id"],
                session_id=session["session_id"],
            )

            async def restart():
                await runtime.aclose()
                await runtime.enter_async_context(server.lifespan(server.app))
                env.dao = server.state.dao

            env.restart = restart
            yield env


async def _wait_for_dispatch(env, state):
    for _ in range(300):
        async with env.dao._sql.connection() as db:
            async with db.execute(
                "SELECT * FROM dispatch_queue WHERE payload_reference = ? AND agent_id = ?",
                (env.raw_log_id, "retry-agent"),
            ) as cursor:
                dispatch = dict(await cursor.fetchone())
        if dispatch["state"] == state:
            # Projection runs after dispatch completion in the same iteration.
            for _ in range(100):
                mutation = await env.dao.get_mutation("retry-agent", env.mutation_id)
                if mutation["state"] in {"COMMITTED", "RETRY_PENDING", "DEAD_LETTER"}:
                    return dispatch, mutation
                await asyncio.sleep(0.05)
            return dispatch, mutation
        await asyncio.sleep(0.05)
    raise AssertionError(f"dispatch did not reach {state}: {dispatch}")


@pytest.mark.asyncio
async def test_v4_transient_provider_failure_commits_stored_facts(
    retry_runtime, immediate_retries
):
    env = retry_runtime
    env.provider.fail_first = 1
    env.start.set()
    dispatch, mutation = await _wait_for_dispatch(env, "FINALIZED")
    assert dispatch["attempt_count"] == 2
    assert mutation["state"] == "COMMITTED"
    assert json.loads(mutation["metadata_json"])["_mesa_v4_projection_triplets"]
    facts = await env.dao.list_v4_assertions_for_mutation(env.mutation_id)
    assert facts and facts[0]["fact_text"] == "MESA preserves durable memory."
    pipeline = await env.dao.get_pipeline_run(mutation["pipeline_run_id"])
    assert pipeline["state"] == "COMMITTED"
    assert (await env.dao.get_raw_log("retry-agent", env.raw_log_id))[
        "status"
    ] == "processed"


@pytest.mark.asyncio
async def test_v4_exhausted_provider_failure_is_visible_as_dead_letter(
    retry_runtime, immediate_retries
):
    env = retry_runtime
    env.provider.fail_first = 99
    env.start.set()
    dispatch, mutation = await _wait_for_dispatch(env, "DEAD_LETTER")
    assert dispatch["attempt_count"] == 5
    assert env.provider.schema_calls == 5
    assert mutation["state"] == "DEAD_LETTER"
    assert (await env.dao.get_raw_log("retry-agent", env.raw_log_id))[
        "status"
    ] == "failed"
    pipeline = await env.dao.get_pipeline_run(mutation["pipeline_run_id"])
    assert pipeline["state"] == "DLQ"
    response = await env.client.get(f"/v4/mutations/{env.mutation_id}")
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "DEAD_LETTER"
    assert response.json()["failure_class"]
    assert (
        await env.dao.get_dispatch_completion_receipt(dispatch["queue_record_id"])
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["VALIDATED", "COMMITTED"])
async def test_v4_validated_redelivery_finalizes_without_model_call(
    retry_runtime, state
):
    env = retry_runtime
    assert await env.dao.record_mutation_extraction("retry-agent", env.mutation_id, [])
    assert await env.dao.set_mutation_state("retry-agent", env.mutation_id, "VALIDATED")
    if state == "COMMITTED":
        for _ in range(3):
            await server.process_projection_outbox_once(
                env.dao, worker_id="crash-setup", limit=100
            )
        assert (await env.dao.get_mutation("retry-agent", env.mutation_id))[
            "state"
        ] == state
    # Simulate death after the durable mutation change, before the raw-log ACK.
    assert (await env.dao.get_raw_log("retry-agent", env.raw_log_id))[
        "status"
    ] == "DEFERRED"
    env.start.set()
    dispatch, mutation = await _wait_for_dispatch(env, "FINALIZED")
    assert env.provider.schema_calls == 0
    assert env.provider.completions == 0
    assert mutation["state"] == "COMMITTED"
    assert (await env.dao.get_raw_log("retry-agent", env.raw_log_id))[
        "status"
    ] == "processed"
    assert await env.dao.get_dispatch_completion_receipt(dispatch["queue_record_id"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "write", ["extraction", "validation", "model_disabled_extraction"]
)
async def test_v4_refused_write_keeps_dispatch_retryable(
    retry_runtime, monkeypatch, write, immediate_retries
):
    env = retry_runtime
    method_name = (
        "set_mutation_state" if write == "validation" else "record_mutation_extraction"
    )
    dao_type = type(env.dao)
    original = getattr(dao_type, method_name)

    async def refused(*args, **kwargs):
        if method_name == "record_mutation_extraction" or args[3] == "VALIDATED":
            return False
        return await original(*args, **kwargs)

    monkeypatch.setattr(dao_type, method_name, refused)
    await server._consume_combined_durable_work_once(
        env.dao,
        consolidation_loop=(
            None
            if write == "model_disabled_extraction"
            else server.state.consolidation_loop
        ),
        model_processing_enabled=write != "model_disabled_extraction",
    )
    raw_log = await env.dao.get_raw_log("retry-agent", env.raw_log_id)
    assert raw_log["status"] == "DEFERRED"
    async with env.dao._sql.connection() as db:
        async with db.execute("SELECT * FROM dispatch_queue") as cursor:
            dispatch = dict(await cursor.fetchone())
    assert dispatch["state"] == "RETRY_PENDING"
    assert (
        await env.dao.get_dispatch_completion_receipt(dispatch["queue_record_id"])
        is None
    )
    monkeypatch.setattr(dao_type, method_name, original)
    env.start.set()
    _, mutation = await _wait_for_dispatch(env, "FINALIZED")
    assert mutation["state"] == "COMMITTED"


async def _consume_once(env):
    return await server._consume_combined_durable_work_once(
        env.dao,
        consolidation_loop=server.state.consolidation_loop,
        model_processing_enabled=True,
    )


async def _raw_row(env):
    async with env.dao._sql.connection() as db:
        async with db.execute(
            "SELECT * FROM raw_logs WHERE id = ?", (env.raw_log_id,)
        ) as cursor:
            return dict(await cursor.fetchone())


async def _retry_row(env, table):
    async with env.dao._sql.connection() as db:
        async with db.execute(
            f"SELECT *, unixepoch(lease_expires_at) - unixepoch('now') AS delay "
            f"FROM {table} WHERE state = 'RETRY_PENDING'"
        ) as cursor:
            row = await cursor.fetchone()
    assert row is not None
    return dict(row)


async def _make_retry_due(env, table):
    # Advance the durable deadline instead of waiting real minutes in tests.
    async with env.dao._sql.transaction() as db:
        await db.execute(
            f"UPDATE {table} SET lease_expires_at = datetime('now', '-1 second') "
            "WHERE state = 'RETRY_PENDING'"
        )
        await db.commit()


@pytest.mark.asyncio
async def test_v4_extraction_backoff_survives_two_failures(retry_runtime):
    env = retry_runtime
    env.provider.fail_first = 2
    for attempt, delay in ((1, 60), (2, 120)):
        assert (await _consume_once(env))["dispatches"] == 1
        row = await _retry_row(env, "dispatch_queue")
        assert row["attempt_count"] == attempt
        if attempt == 1:
            await env.restart()  # the retry deadline survives reopening storage
        # The production consumer must leave the future retry unclaimed.
        assert (await _consume_once(env))["dispatches"] == 0
        assert env.provider.schema_calls == attempt
        assert delay - 1 <= row["delay"] <= delay
        assert row["claim_token"] is None and row["claimed_by"] is None
        await _make_retry_due(env, "dispatch_queue")
    env.start.set()
    dispatch, mutation = await _wait_for_dispatch(env, "FINALIZED")
    assert dispatch["attempt_count"] == 3
    assert env.provider.schema_calls == 3
    assert mutation["state"] == "COMMITTED"
    assert await env.dao.list_v4_assertions_for_mutation(env.mutation_id)


@pytest.mark.asyncio
async def test_v4_vector_backoff_recovers_after_provider_failure(retry_runtime):
    env = retry_runtime
    await _consume_once(env)  # extraction and SQL projection
    env.provider.fail_embeddings = 1
    await _consume_once(env)  # VECTOR provider failure
    row = await _retry_row(env, "projection_outbox")
    assert row["projection_name"] == "VECTOR"
    assert row["attempt_count"] == 1
    assert (await _consume_once(env))["projections"] == 0
    assert 59 <= row["delay"] <= 60
    assert (await _retry_row(env, "projection_outbox"))["attempt_count"] == 1
    await _make_retry_due(env, "projection_outbox")
    env.start.set()
    mutation = await _wait_for_committed(env.dao, env.mutation_id)
    assert mutation["state"] == "COMMITTED"
    assert env.provider.schema_calls == 1


@pytest.mark.asyncio
async def test_v4_restart_recovers_unexpired_raw_log_after_validation(
    retry_runtime, monkeypatch
):
    env = retry_runtime
    dao_type = type(env.dao)
    transition = dao_type.transition_claimed_raw_log

    async def crash_before_ack(self, *args, **kwargs):
        if kwargs.get("status") == "processed":
            raise asyncio.CancelledError("crash after VALIDATED, before raw-log ACK")
        return await transition(self, *args, **kwargs)

    async def hold_projection(*args, **kwargs):
        return {"completed": 0}

    with monkeypatch.context() as crash:
        crash.setattr(server, "process_projection_outbox_once", hold_projection)
        crash.setattr(dao_type, "transition_claimed_raw_log", crash_before_ack)
        await _consume_once(env)
    assert (await env.dao.get_mutation("retry-agent", env.mutation_id))[
        "state"
    ] == "VALIDATED"
    raw = await _raw_row(env)
    assert raw["status"] == "processing" and raw["claim_token"]
    # Only the 300s dispatch lease has elapsed; the raw-log lease lasts 1800s.
    async with env.dao._sql.transaction() as db:
        await db.execute(
            "UPDATE dispatch_queue SET lease_expires_at = datetime('now', '-1 second')"
        )
        await db.commit()
    await env.dao.initialize()  # tools must leave live runtime claims alone
    assert (await _raw_row(env))["status"] == "processing"
    await env.restart()
    raw = await _raw_row(env)
    assert raw["status"] == "DEFERRED"
    assert raw["claim_token"] is None and raw["claimed_by"] is None
    assert raw["lease_expires_at"] is None
    env.start.set()
    dispatch, mutation = await _wait_for_dispatch(env, "FINALIZED")
    assert mutation["state"] == "COMMITTED"
    assert dispatch["attempt_count"] == 2
    assert env.provider.schema_calls == 1
    assert (await env.dao.get_raw_log("retry-agent", env.raw_log_id))[
        "status"
    ] == "processed"


@pytest.mark.asyncio
async def test_v4_extraction_dead_letter_replays_through_http(retry_runtime):
    env = retry_runtime
    env.provider.fail_first = 5
    for _ in range(5):
        await _consume_once(env)
        await _make_retry_due(env, "dispatch_queue")
    mutation = await env.dao.get_mutation("retry-agent", env.mutation_id)
    assert mutation["state"] == "DEAD_LETTER"
    assert (await env.dao.get_raw_log("retry-agent", env.raw_log_id))[
        "status"
    ] == "failed"
    # A failed raw-log write must roll back the dispatch and mutation replay too.
    async with env.dao._sql.transaction() as db:
        await db.execute(
            "CREATE TRIGGER fail_replay_raw_log BEFORE UPDATE OF status ON raw_logs "
            "WHEN OLD.status = 'failed' AND NEW.status = 'DEFERRED' "
            "BEGIN SELECT RAISE(ABORT, 'injected replay failure'); END"
        )
        await db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="injected replay failure"):
        await env.dao.replay_pipeline_run(mutation["pipeline_run_id"])
    assert (await env.dao.get_mutation("retry-agent", env.mutation_id))[
        "state"
    ] == "DEAD_LETTER"
    assert (await env.dao.get_pipeline_run(mutation["pipeline_run_id"]))[
        "state"
    ] == "DLQ"
    assert (await _raw_row(env))["status"] == "failed"
    async with env.dao._sql.transaction() as db:
        async with db.execute(
            "SELECT state, attempt_count FROM dispatch_queue"
        ) as cursor:
            dispatch = await cursor.fetchone()
        assert tuple(dispatch) == ("DEAD_LETTER", 5)
        await db.execute("DROP TRIGGER fail_replay_raw_log")
        await db.commit()
    access = server.state.access_control
    await access.grant_access("retry-agent", env.session_id, "WRITE")
    await access.grant_principal_session_access(
        "retry-principal", "retry-agent", env.session_id, "WRITE"
    )
    await access.grant_scope_role(
        "retry-principal",
        tenant_id="retry-tenant",
        workspace_id="default",
        dataset_id="main",
        role="WRITER",
    )
    await access.grant_dataset_permission(
        "retry-principal",
        tenant_id="retry-tenant",
        dataset_id="main",
        permission="ROLLBACK",
    )
    response = await env.client.post(f"/v4/mutations/{env.mutation_id}/replay")
    assert response.status_code == 202, response.text
    assert (await env.dao.get_mutation("retry-agent", env.mutation_id))[
        "state"
    ] == "RETRY_PENDING"
    row = await _retry_row(env, "dispatch_queue")
    assert row["attempt_count"] == 0
    assert row["lease_expires_at"] is None and row["last_error_class"] is None
    assert row["claim_token"] is None and row["claimed_by"] is None
    raw = await _raw_row(env)
    assert raw["status"] == "DEFERRED" and raw["claim_token"] is None
    env.start.set()
    dispatch, mutation = await _wait_for_dispatch(env, "FINALIZED")
    assert dispatch["attempt_count"] == 1
    assert env.provider.schema_calls == 6
    assert mutation["state"] == "COMMITTED"
    assert await env.dao.list_v4_assertions_for_mutation(env.mutation_id)


def test_retry_delay_is_exponential_and_capped():
    assert [dao_module._retry_delay_seconds(attempt) for attempt in range(1, 8)] == [
        60,
        120,
        240,
        480,
        900,
        900,
        900,
    ]
