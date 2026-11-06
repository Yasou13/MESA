"""A0: authenticated health must progress during maximum-size search scoring."""

import asyncio
import time

import pytest

from mesa_memory.api import server
from tests.test_model_optional_runtime import model_runtime as model_runtime
from tests.test_p908_search_limits import _insert, _search


def _worst_query():
    probes = ["z" * 7 + c for c in "abcdefghijklmnopqrstuvwxy"] + [
        "z" * 6 + a + b for a in "xy" for b in "abcdefghijklmnopqrstuvwxy"
    ]
    # Include zz in the passage FTS's first 12 tokens; the remaining probes
    # repeatedly scan the entire stored z-run without finding a match.
    return " ".join([*probes[:11], "zz", *probes[11:63]]).ljust(4096)


@pytest.mark.no_cover
@pytest.mark.asyncio
@pytest.mark.parametrize("count", [160, 1000])
async def test_health_stays_responsive_while_scoring_large_memories(
    model_runtime, count
):
    async with model_runtime(
        MESA_MODEL_ENABLED=False,
        MESA_GRAPH_ENABLED=False,
        MESA_RATE_LIMIT_PER_MINUTE=100000,
    ) as env:
        for index in range(count):
            # Deliberately omit evidence_span: it must default to all 32 KB,
            # as in Opus's corrected repro, rather than a short excerpt.
            await _insert(env, index, "zz " + "z" * 32765)
        env.client.headers["X-API-Key"] = await server.state.api_key_store.issue_key(
            principal_id="model-principal"
        )
        assert len(await _search(env, "zz")) == 50
        query = _worst_query()
        assert len(query.split()) == 64
        search = asyncio.create_task(_search(env, query))
        health_latencies = []
        try:
            while not search.done():
                scheduled = time.perf_counter() + 0.02
                await asyncio.sleep(0.02)
                response = await env.client.get("/health")
                # Include time the event loop delayed starting the request.
                health_latencies.append(time.perf_counter() - scheduled)
                assert response.status_code == 200
            results = await search
            assert len(results) == 50
        finally:
            if not search.done():
                search.cancel()
            await asyncio.gather(search, return_exceptions=True)
        assert health_latencies
        assert max(health_latencies) < 0.2, (
            f"/health was blocked for {max(health_latencies):.3f}s during scoring"
        )


@pytest.mark.asyncio
async def test_public_health_probes_storage_and_keeps_full_diagnostics_available(
    model_runtime, monkeypatch
):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        statements = []
        original = env.dao._sql._apply_pragmas

        async def traced_pragmas(db):
            await db.set_trace_callback(statements.append)
            await original(db)

        monkeypatch.setattr(env.dao._sql, "_apply_pragmas", traced_pragmas)
        response = await env.client.get("/health")
        assert response.status_code == 200
        assert response.json() == {
            "status": "healthy",
            "maintenance": False,
            "rebuild_state": "IDLE",
        }
        assert any(sql.strip().upper() == "SELECT 1;" for sql in statements)
        assert not any("quick_check" in sql.lower() for sql in statements)
        statements.clear()
        detailed = await env.dao.health_check()
        assert detailed["sqlite"]["integrity"] == "ok"
        assert "v4_projection" in detailed
        assert "v4_ownership" in detailed
        assert "v4_vector_consumer" in detailed
        assert any("quick_check" in sql.lower() for sql in statements)
