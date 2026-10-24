"""Retry and crash redelivery through the real combined runtime (F-01)."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from mesa_memory.api import server
from mesa_memory.config import configured_embedding_identity
from tests.test_d008_model_enabled_runtime_e2e import (
    _DeterministicProvider,
    _embedding_service,
)


class _FlakyProvider(_DeterministicProvider):
    def __init__(self) -> None:
        super().__init__()
        self.fail_first = 0
        self.schema_calls = 0

    def complete(self, prompt: str, schema: Any = None, **kwargs: Any) -> Any:
        if schema is not None:
            self.schema_calls += 1
            if self.schema_calls <= self.fail_first:
                raise ConnectionError("temporary provider outage")
        return super().complete(prompt, schema, **kwargs)


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
    async with server.lifespan(server.app):
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
            yield SimpleNamespace(
                dao=dao,
                provider=provider,
                start=start,
                client=client,
                mutation_id=admitted["response"]["mutation_id"],
                raw_log_id=admitted["response"]["raw_log_id"],
            )


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
async def test_v4_transient_provider_failure_commits_stored_facts(retry_runtime):
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
async def test_v4_exhausted_provider_failure_is_visible_as_dead_letter(retry_runtime):
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
    retry_runtime, monkeypatch, write
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
