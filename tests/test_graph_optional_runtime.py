"""D28 extends D14's skipped-lane lifecycle to GRAPH."""

import argparse
import hashlib
import sqlite3

import pytest

from mesa_memory.api import server
from mesa_storage.rebuild_preparation import _backlog_counts
from tests.test_model_optional_runtime import (
    _assert_committed,
    _consume,
    _insert,
    _lanes,
    _search,
)
from tests.test_model_optional_runtime import model_runtime as model_runtime


@pytest.mark.asyncio
async def test_graph_disabled_never_opens_kuzu(model_runtime, monkeypatch):
    original = server.kuzu.Database
    calls = []

    def track(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(server.kuzu, "Database", track)
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        assert not server._KUZU_PATH.exists()
        assert calls == []
        assert server.state.dao._graph is None
        assert server.state.runtime_profile.graph_enabled is False
        await _insert(env)
        await _consume(env, 3)
        await _assert_committed(env)
        await _search(env)
        assert calls == []


@pytest.mark.asyncio
async def test_graph_disabled_commits_skipped_lane(model_runtime):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        await _insert(env)
        await _consume(env, 3)
        await _assert_committed(env)
        assert (await _lanes(env))["GRAPH"] == "SKIPPED"
        with sqlite3.connect(
            server.state.runtime_profile.storage_root / "mesa.db"
        ) as db:
            assert _backlog_counts(db)["projection"] == 0
        assert env.provider.embeddings == env.provider.completions == 0


@pytest.mark.asyncio
async def test_graph_disabled_search_reports_degradation(model_runtime):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        await _insert(env)
        await _consume(env, 3)
        assert (await _search(env))["degraded_sources"] == ["vector", "graph"]


@pytest.mark.asyncio
async def test_graph_disabled_capability_is_truthful(model_runtime):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        response = await env.client.get("/v4/capability")
        assert response.status_code == 200, response.text
        capabilities = response.json()["capabilities"]
        assert capabilities["graph_projection"] is False
        assert capabilities["graph_neighbor_retrieval"] is False


@pytest.mark.asyncio
async def test_graph_disabled_runtime_is_ready(model_runtime):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        response = await env.client.get("/health/init")
        assert response.status_code == 200, response.text
        assert response.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_restart_backfills_skipped_graph_without_hiding_records(model_runtime):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        await _insert(env)
        await _consume(env, 3)
        assert (await _lanes(env))["GRAPH"] == "SKIPPED"
        await env.restart(MESA_GRAPH_ENABLED=True)
        assert (await _lanes(env))["GRAPH"] == "PENDING"
        await _assert_committed(env)
        assert "graph" in (await _search(env))["degraded_sources"]
        await _consume(env)
        assert (await _lanes(env))["GRAPH"] == "COMPLETED"
        result = await _search(env)
        assert result["degraded_sources"] == ["vector"]
        assert any(
            "graph" in r["retrieval_provenance"]["origins"] for r in result["results"]
        )


@pytest.mark.asyncio
async def test_failed_graph_backfill_keeps_committed_evidence(
    model_runtime, monkeypatch
):
    from mesa_memory.graph.projector import GraphProjector

    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        await _insert(env)
        await _consume(env, 3)
        assert (await _lanes(env))["GRAPH"] == "SKIPPED"
        await env.restart(MESA_GRAPH_ENABLED=True)

        async def fail(*args, **kwargs):
            raise RuntimeError("injected graph outage")

        monkeypatch.setattr(GraphProjector, "project_assertion", fail)
        for attempt in range(1, 6):
            await _consume(env)
            assert (await _lanes(env))["GRAPH"] == (
                "DEAD_LETTER" if attempt == 5 else "RETRY_PENDING"
            )
            await _assert_committed(env)
            assert "graph" in (await _search(env))["degraded_sources"]
            async with env.dao._sql.transaction() as db:
                await db.execute(
                    "UPDATE projection_outbox SET lease_expires_at = datetime('now', '-1 second') WHERE state = 'RETRY_PENDING'"
                )
                await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "originally_enabled", [False, True], ids=["skipped", "existing-graph"]
)
@pytest.mark.parametrize(
    "fail_after_cutover", [False, True], ids=["success", "rollback"]
)
async def test_rebuild_with_graph_disabled_never_opens_kuzu(
    model_runtime, monkeypatch, tmp_path, originally_enabled, fail_after_cutover
):
    from mesa_memory import rebuild_runner
    from mesa_storage.rebuild_cutover import ProjectionParityVerifier
    from mesa_storage.repositories.operations import OperationRepository
    from mesa_storage.sqlite_engine import AsyncEngine
    from tests.test_d008_model_enabled_runtime_e2e import _embedding_service

    async with model_runtime(
        MESA_GRAPH_ENABLED=originally_enabled, MESA_V4_REBUILD_ENABLED=True
    ) as env:
        await _insert(env)
        await _consume(env, 3)
        storage_root = server.state.runtime_profile.storage_root
        await env.stop()
        engine = AsyncEngine(str(storage_root / "mesa.db"))
        await engine.initialize()
        try:
            operation = await OperationRepository(engine).submit(
                requested_by_principal_id="model-principal",
                idempotency_key="graph-disabled-rebuild",
                payload_hash=hashlib.sha256(b"graph-disabled-rebuild").hexdigest(),
            )
        finally:
            await engine.close()
        monkeypatch.setenv("MESA_GRAPH_ENABLED", "false")
        monkeypatch.setattr(
            rebuild_runner,
            "get_embedding_service",
            lambda **kw: _embedding_service(env.provider),
        )
        verify = ProjectionParityVerifier.verify
        verifications = 0

        async def fail_post_cutover(self, **kwargs):
            nonlocal verifications
            verifications += 1
            if verifications == 2:
                raise RuntimeError("injected post-cutover failure")
            return await verify(self, **kwargs)

        if fail_after_cutover:
            monkeypatch.setattr(ProjectionParityVerifier, "verify", fail_post_cutover)
        calls = []
        original = server.kuzu.Database

        def track(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)

        with monkeypatch.context() as guard:
            guard.setattr(server.kuzu, "Database", track)
            work_root = tmp_path / "rebuild-work"
            work_root.mkdir()
            rc = await rebuild_runner.run_rebuild(
                argparse.Namespace(
                    trusted_root=tmp_path,
                    storage_root=storage_root,
                    work_root=work_root,
                    operation_id=operation["operation_id"],
                    batch_size=100,
                    lease_seconds=300,
                )
            )
            assert rc == (
                rebuild_runner.EXIT_RETRYABLE
                if fail_after_cutover
                else rebuild_runner.EXIT_OK
            )
            assert calls == []
            await env.restart(MESA_GRAPH_ENABLED=False)
            if fail_after_cutover:
                assert (await _lanes(env))["GRAPH"] == (
                    "COMPLETED" if originally_enabled else "SKIPPED"
                )
                await _assert_committed(env)
                assert "graph" in (await _search(env))["degraded_sources"]
                return
            assert not server._KUZU_PATH.exists()
            assert calls == []
            await _assert_committed(env)
            assert (await _lanes(env))["GRAPH"] == "SKIPPED"
            assert "graph" in (await _search(env))["degraded_sources"]
        await env.restart(MESA_GRAPH_ENABLED=True)
        assert (await _lanes(env))["GRAPH"] == "PENDING"
        await _consume(env)
        assert (await _lanes(env))["GRAPH"] == "COMPLETED"
        assert "graph" not in (await _search(env))["degraded_sources"]


def test_graph_flag_defaults_true_and_test_isolated_keeps_its_rule(tmp_path):
    from mesa_memory.config import load_runtime_profile

    runtime = load_runtime_profile(
        {
            "MESA_RUNTIME_PROFILE": "combined",
            "MESA_STORAGE_ROOT": str(tmp_path / "normal"),
        }
    )
    assert getattr(runtime, "graph_enabled", None) is True
    isolated = {
        "MESA_RUNTIME_PROFILE": "test-isolated",
        "MESA_RUNTIME_LAB_ROOT": str(tmp_path),
        "MESA_STORAGE_ROOT": str(tmp_path / "isolated"),
    }
    assert load_runtime_profile(isolated).worker_enabled is False
    assert (
        load_runtime_profile({**isolated, "MESA_GRAPH_ENABLED": "false"}).graph_enabled
        is False
    )
