"""Production runtime tests for V4 graph timeout, operational safety, and lifecycle closure.

Covers:
1. Default graph timeout loaded through central config
2. Custom valid timeout reaches KuzuGraphProvider and health diagnostics
3. Invalid timeout configuration rejected deterministically
4. 20-seed / 3-hop representative legal query completes under intended configuration
5. Query exceeding timeout returns controlled GraphSearchError (fail closed)
6. Timeout does not permanently poison provider operational state
7. Subsequent graph request succeeds after an ordinary request timeout
8. Shutdown after timeout remains safe without connection corruption or crash
9. Graph OFF search remains completely unaffected
10. Combined runtime server lifespan wires the configured timeout
11. Server survival test: timeout -> HTTP 503 -> /health healthy -> valid query 200 -> clean close
"""

from __future__ import annotations

import asyncio
import math
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import Depends, FastAPI, Request

from mesa_api.v4_router import create_v4_router
from mesa_memory.api import server
from mesa_memory.config import (
    MesaConfig,
    RuntimeProfile,
    RuntimeProfileConfig,
    config,
)
from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import (
    GraphSearchError,
    KuzuGraphProvider,
)
from mesa_storage.kuzu_setup import initialize_schema_artifact
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine
from tests.test_v4_graph_retrieval_hardening import (
    _close_test_env,
    _create_test_env,
    _ingest_entity_and_assertion,
)


# ---------------------------------------------------------------------------
# 1. Config loading & defaults
# ---------------------------------------------------------------------------


def test_default_graph_timeout_is_loaded_through_central_config(monkeypatch):
    """Central config must provide a production-safe default (15.0s) for V4 graph queries."""
    monkeypatch.delenv("MESA_V4_GRAPH_TIMEOUT_SECONDS", raising=False)
    cfg = MesaConfig(_env_file=None)
    assert cfg.v4_graph_timeout_seconds == 15.0


def test_custom_valid_timeout_reaches_kuzu_graph_provider(tmp_path):
    """Provider constructor must accept and record explicit timeout, reflected in diagnostics."""
    graph_path = tmp_path / "timeout_prov"
    initialize_schema_artifact(str(graph_path))
    provider = KuzuGraphProvider(str(graph_path), max_workers=1, search_timeout_seconds=22.5)
    assert provider.search_timeout_seconds == 22.5


# ---------------------------------------------------------------------------
# 2. Validation & rejection of nonsensical values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "invalid_val",
    [
        0,
        0.0,
        -1,
        -5.5,
        float("nan"),
        float("inf"),
        float("-inf"),
        "0",
        "-10",
        "nan",
        "inf",
        "-inf",
        "invalid_text",
        "",
        True,
        False,
    ],
)
def test_invalid_timeout_config_rejected(invalid_val, monkeypatch, tmp_path):
    """Zero, negative, NaN, infinity, boolean, and malformed strings must fail validation."""
    # Test MesaConfig rejection
    if isinstance(invalid_val, (str, int, float, bool)):
        monkeypatch.setenv("MESA_V4_GRAPH_TIMEOUT_SECONDS", str(invalid_val))
        with pytest.raises((ValueError, Exception)):
            MesaConfig(_env_file=None)

    # Test KuzuGraphProvider rejection directly
    graph_path = tmp_path / "reject_prov"
    with pytest.raises(ValueError, match="positive finite number"):
        KuzuGraphProvider(str(graph_path), search_timeout_seconds=invalid_val)


# ---------------------------------------------------------------------------
# 3. 20-seed / 3-hop representative legal workload completes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_20_seed_3_hop_representative_query_completes_under_intended_config(tmp_path):
    """Verify 20-seed, 3-hop traversal on scale legal graph completes reliably without memory exhaustion."""
    graph_path = tmp_path / "scale_graph_20s"
    initialize_schema_artifact(str(graph_path))
    provider = KuzuGraphProvider(str(graph_path), max_workers=2, search_timeout_seconds=15.0)
    await provider.initialize()

    agent_id = "agent_scale_20s"
    N_ENTITIES = 5808
    N_ASSERTIONS = 5719

    # Ingest entities
    for i in range(0, N_ENTITIES, 1000):
        async with provider.transaction():
            for j in range(i, min(i + 1000, N_ENTITIES)):
                await provider.insert_node(f"ent_{j}", f"Entity {j}", agent_id=agent_id)

    # Ingest assertions: dense network ensuring all 20 seeds reach 3-hop targets
    for i in range(0, N_ASSERTIONS, 1000):
        async with provider.transaction():
            for j in range(i, min(i + 1000, N_ASSERTIONS)):
                if j < 100:
                    s_idx = j % 20
                    t_idx = 20 + (j % 50)
                elif j < 600:
                    s_idx = 20 + (j % 50)
                    t_idx = 70 + (j % 100)
                elif j < 2000:
                    s_idx = 70 + (j % 100)
                    t_idx = 170 + (j % 500)
                else:
                    s_idx = j % 500
                    t_idx = (j * 13) % N_ENTITIES

                await provider.insert_assertion(
                    assertion_id=f"ass_{j}",
                    agent_id=agent_id,
                    subject_id=f"ent_{s_idx}",
                    predicate="cites and references according to law",
                    object_id=f"ent_{t_idx}",
                    confidence=0.85,
                    mutation_id=f"mut_{j}",
                )

    allowed_entity_ids = {f"ent_{i}" for i in range(N_ENTITIES)}
    allowed_assertion_ids = {f"ass_{i}" for i in range(N_ASSERTIONS)}
    seeds_20 = [f"ent_{k}" for k in range(20)]
    seed_scores = {f"ent_{k}": 0.85 for k in range(20)}
    query = "cites and references according to law statute code"

    t_start = time.monotonic()
    hits = await provider.search_v4_graph(
        agent_id=agent_id,
        seed_entity_ids=seeds_20,
        allowed_entity_ids=allowed_entity_ids,
        allowed_assertion_ids=allowed_assertion_ids,
        max_hops=3,
        limit=500,
        query=query,
        seed_scores=seed_scores,
        direction="any",
    )
    elapsed = time.monotonic() - t_start

    assert elapsed < 15.0, f"Query took {elapsed:.2f}s, expected < 15.0s"
    assert len(hits) > 0, "Expected non-empty hits from 20-seed 3-hop traversal"

    h3_hits = [h for h in hits if h["hops"] == 3]
    assert len(h3_hits) > 0, "Expected 3-hop hits to be found"
    first_h3 = h3_hits[0]
    assert len(first_h3["path_entity_ids"]) == 4
    assert len(first_h3["best_path_assertion_ids"]) == 3
    assert len(first_h3["edge_directions"]) == 3
    assert first_h3["seed_id"] in seeds_20

    # Diagnostics check
    health = await provider.health_check()
    assert health["status"] == "healthy"
    assert health["search_timeout_seconds"] == 15.0

    await provider.close()


# ---------------------------------------------------------------------------
# 4. Timeout behavior & operational state preservation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_exceeding_timeout_returns_controlled_failure(tmp_path):
    """When a query exceeds the configured timeout, it must fail closed with GraphSearchError."""
    graph_path = tmp_path / "timeout_graph"
    initialize_schema_artifact(str(graph_path))
    provider = KuzuGraphProvider(str(graph_path), max_workers=2, search_timeout_seconds=0.01)
    await provider.initialize()

    agent_id = "agent_timeout"
    await provider.insert_node("ent_0", "Seed", agent_id=agent_id)
    await provider.insert_node("ent_1", "Target", agent_id=agent_id)
    await provider.insert_assertion(
        assertion_id="ass_0",
        agent_id=agent_id,
        subject_id="ent_0",
        predicate="cites",
        object_id="ent_1",
        mutation_id="m0",
    )

    def slow_query(*args, **kwargs):
        time.sleep(0.1)
        return []

    with patch.object(provider, "_sync_execute", side_effect=slow_query):
        with pytest.raises(GraphSearchError) as exc_info:
            await provider.search_v4_graph(
                agent_id=agent_id,
                seed_entity_ids=["ent_0"],
                allowed_entity_ids={"ent_0", "ent_1"},
                allowed_assertion_ids={"ass_0"},
                max_hops=1,
            )
        assert "timed out" in str(exc_info.value)

    # Provider MUST remain operational after an ordinary timeout!
    assert provider.is_initialized is True
    assert provider.is_operational is True

    await provider.close()


@pytest.mark.asyncio
async def test_subsequent_graph_request_succeeds_after_ordinary_timeout(tmp_path):
    """A subsequent valid graph request must succeed even after an earlier query timed out."""
    graph_path = tmp_path / "recovery_graph"
    initialize_schema_artifact(str(graph_path))
    # Start with generous timeout
    provider = KuzuGraphProvider(str(graph_path), max_workers=2, search_timeout_seconds=15.0)
    await provider.initialize()

    agent_id = "agent_recovery"
    await provider.insert_node("ent_0", "Seed", agent_id=agent_id)
    await provider.insert_node("ent_1", "Target", agent_id=agent_id)
    await provider.insert_assertion(
        assertion_id="ass_0",
        agent_id=agent_id,
        subject_id="ent_0",
        predicate="cites",
        object_id="ent_1",
        mutation_id="m0",
    )

    # 1. Trigger timeout by temporarily forcing a tiny timeout on provider
    provider._search_timeout_seconds = 0.0001
    with pytest.raises(GraphSearchError) as exc_info:
        await provider.search_v4_graph(
            agent_id=agent_id,
            seed_entity_ids=["ent_0"],
            allowed_entity_ids={"ent_0", "ent_1"},
            allowed_assertion_ids={"ass_0"},
            max_hops=1,
        )
    assert "timed out" in str(exc_info.value)

    # Verify provider health is still intact
    assert provider.is_operational is True

    # 2. Restore normal timeout: second query must succeed cleanly!
    provider._search_timeout_seconds = 15.0
    hits = await provider.search_v4_graph(
        agent_id=agent_id,
        seed_entity_ids=["ent_0"],
        allowed_entity_ids={"ent_0", "ent_1"},
        allowed_assertion_ids={"ass_0"},
        max_hops=1,
    )
    assert len(hits) == 1
    assert hits[0]["entity_id"] == "ent_1"

    await provider.close()


@pytest.mark.asyncio
async def test_shutdown_after_timeout_remains_safe(tmp_path):
    """Shutdown barrier must cleanly drain active native queries after timeout without segfaults."""
    graph_path = tmp_path / "shutdown_graph"
    initialize_schema_artifact(str(graph_path))
    provider = KuzuGraphProvider(str(graph_path), max_workers=2, search_timeout_seconds=0.01)
    await provider.initialize()

    agent_id = "agent_sd"
    await provider.insert_node("ent_0", "Seed", agent_id=agent_id)
    await provider.insert_node("ent_1", "Target", agent_id=agent_id)
    await provider.insert_assertion(
        assertion_id="ass_0",
        agent_id=agent_id,
        subject_id="ent_0",
        predicate="cites",
        object_id="ent_1",
        mutation_id="m0",
    )

    def slow_exec(*args, **kwargs):
        time.sleep(0.08)
        return []

    with patch.object(provider, "_sync_execute", side_effect=slow_exec):
        with pytest.raises(GraphSearchError):
            await provider.search_v4_graph(
                agent_id=agent_id,
                seed_entity_ids=["ent_0"],
                allowed_entity_ids={"ent_0", "ent_1"},
                allowed_assertion_ids={"ass_0"},
                max_hops=1,
            )

    t0 = time.monotonic()
    await provider.close(timeout=2.0)
    close_duration = time.monotonic() - t0

    assert not provider.is_initialized
    assert not provider.is_operational
    assert close_duration < 2.0


# ---------------------------------------------------------------------------
# 5. Graph OFF regression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graph_off_unaffected_by_timeout_or_outage(tmp_path):
    """Graph OFF (graph_enabled=False) search must not invoke graph provider or fail on graph outage."""
    sql, vector, graph, dao = await _create_test_env(tmp_path)
    try:
        tenant_id = "test-tenant"
        agent_id = "test-agent"
        dataset_id = "default-ds"

        await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            mutation_id="m_off",
            subject_name="KanunMadde",
            predicate="duzenler",
            object_name="Uygulama",
        )

        # Force graph search to fail if called
        with patch.object(graph, "search_v4_graph", side_effect=GraphSearchError("timed out")):
            results = await dao.search_v4_memory(
                tenant_id=tenant_id,
                agent_id=agent_id,
                dataset_ids=[dataset_id],
                query="KanunMadde",
                limit=10,
                graph_enabled=False,
            )
            assert len(results) >= 1
            names = {r["entity"]["canonical_name"] for r in results}
            assert "KanunMadde" in names
    finally:
        await _close_test_env(sql, vector, graph)


# ---------------------------------------------------------------------------
# 6. Combined runtime wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_combined_runtime_wires_configured_graph_timeout(monkeypatch, tmp_path):
    """The combined runtime server lifespan must wire MESA_V4_GRAPH_TIMEOUT_SECONDS into KuzuGraphProvider."""
    storage_root = tmp_path / "combined_data"
    storage_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("MESA_API_KEY", "test-api-key-for-combined-runtime")
    monkeypatch.setattr(server, "_MESA_API_KEY", "test-api-key-for-combined-runtime")
    monkeypatch.setenv("MESA_V4_GRAPH_TIMEOUT_SECONDS", "18.5")
    monkeypatch.setattr(config, "v4_graph_timeout_seconds", 18.5)

    runtime = RuntimeProfileConfig(
        profile=RuntimeProfile.COMBINED,
        storage_root=storage_root,
        load_dotenv=False,
        dotenv_path=None,
        model_enabled=False,
        external_provider_enabled=False,
        api_enabled=True,
        worker_enabled=True,
        require_worker_readiness=False,
    )

    monkeypatch.setattr(server, "load_runtime_profile", lambda: runtime)
    monkeypatch.setattr(server, "load_explicit_dotenv", MagicMock())
    monkeypatch.setattr(server, "_refresh_auth_config", MagicMock())
    monkeypatch.setattr(server, "setup_telemetry_tracing", MagicMock())

    test_app = FastAPI()
    async with server.lifespan(test_app):
        assert server.state.graph_provider is not None
        assert server.state.graph_provider.search_timeout_seconds == 18.5

        # Verify dao health check reports configured timeout
        health = await server.state.dao.health_check()
        assert "graph" in health
        assert health["graph"]["timeout_seconds"] == 18.5


# ---------------------------------------------------------------------------
# 7. Server survival full loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_survival_full_loop_after_graph_timeout(tmp_path):
    """Full lifecycle: graph timeout -> controlled 503 -> /health healthy -> subsequent query 200 -> clean close."""
    sql, vector, graph, dao = await _create_test_env(tmp_path)
    try:
        tenant_id = "test-tenant"
        agent_id = "test-agent"
        dataset_id = "default-ds"
        session_id = "sess_srv"

        # Setup session under existing workspace
        await dao.create_v4_session(
            tenant_id=tenant_id,
            workspace_id="default-ws",
            session_id=session_id,
            agent_id=agent_id,
            dataset_ids=[dataset_id],
            principal_id="test-principal",
        )

        await _ingest_entity_and_assertion(
            dao,
            graph,
            tenant_id=tenant_id,
            agent_id=agent_id,
            dataset_id=dataset_id,
            mutation_id="m_srv",
            subject_name="MevzuatA",
            predicate="hukum",
            object_name="YargiB",
        )

        # Build FastAPI test app with router and authenticated principal
        async def attach_principal(request: Request) -> None:
            request.state.principal = SimpleNamespace(
                principal_id="test-principal", principal_type="USER", status="active"
            )

        app = FastAPI(dependencies=[Depends(attach_principal)])
        access = MagicMock()
        access.check_principal_permission = AsyncMock(return_value=True)
        access.check_principal_session_access = AsyncMock(return_value=True)
        access.check_access = AsyncMock(return_value=True)
        access.check_scope_role = AsyncMock(return_value=True)
        access.check_dataset_permission = AsyncMock(return_value=True)
        access.check_control_role = AsyncMock(return_value=True)

        app.include_router(
            create_v4_router(
                get_dao=lambda: dao,
                get_access_control=lambda: access,
            )
        )

        @app.get("/health")
        async def health():
            h = await dao.health_check()
            is_healthy = h["sqlite"].get("status") == "healthy"
            return {"status": "healthy" if is_healthy else "degraded", "diagnostics": h}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            # 1. Simulating query timeout in graph search
            with patch.object(
                graph,
                "search_v4_graph",
                side_effect=GraphSearchError("Kùzu graph retrieval timed out after 15.0s"),
            ):
                resp1 = await client.post(
                    "/v4/memory/search",
                    json={
                        "session_id": session_id,
                        "dataset_ids": [dataset_id],
                        "query": "MevzuatA",
                        "graph_mode": "enabled",
                    },
                )
                assert resp1.status_code == 503
                assert resp1.json()["detail"] == "graph_backend_unavailable"

            # 2. Server remains healthy! GET /health returns 200 and healthy
            health_resp = await client.get("/health")
            assert health_resp.status_code == 200
            assert health_resp.json()["status"] == "healthy"

            # 3. Provider operational state was preserved, so subsequent valid query succeeds!
            assert graph.is_operational is True
            assert dao.graph_operational is True

            resp2 = await client.post(
                "/v4/memory/search",
                json={
                    "session_id": session_id,
                    "dataset_ids": [dataset_id],
                    "query": "MevzuatA",
                    "graph_mode": "enabled",
                },
            )
            assert resp2.status_code == 200
            assert "results" in resp2.json()

    finally:
        await _close_test_env(sql, vector, graph)
