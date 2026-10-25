"""P2-04: vector availability is reported for the authorized search corpus."""

import pytest

from tests.test_model_optional_runtime import (
    _consume,
    _insert,
    _search,
)
from tests.test_model_optional_runtime import (
    model_runtime as model_runtime,
)


@pytest.mark.asyncio
async def test_embedding_off_reports_vector_degradation(model_runtime):
    async with model_runtime() as env:
        await _insert(env)
        await _consume(env, 3)
        result = await _search(env)
        assert "degraded_sources" in result
        assert result["degraded_sources"] == ["vector"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lane_state", ["SKIPPED", "PENDING", "RETRY_PENDING", "DEAD_LETTER", "COMPLETED"]
)
async def test_degradation_tracks_committed_vector_lane(model_runtime, lane_state):
    async with model_runtime(MESA_EMBEDDING_ENABLED=True) as env:
        await _insert(env)
        await _consume(env, 3)
        async with env.dao._sql.transaction() as db:
            await db.execute(
                "UPDATE projection_outbox SET state = ? "
                "WHERE mutation_id = ? AND projection_name = 'VECTOR'",
                (lane_state, env.mutation_id),
            )
            await db.commit()
        result = await _search(env)
        assert "degraded_sources" in result
        assert result["degraded_sources"] == (
            [] if lane_state == "COMPLETED" else ["vector"]
        )


@pytest.mark.asyncio
async def test_excluded_assertions_do_not_degrade_the_search(model_runtime):
    async with model_runtime(MESA_EMBEDDING_ENABLED=True) as env:
        await _insert(env)
        await _consume(env, 3)
        async with env.dao._sql.transaction() as db:
            await db.execute(
                "UPDATE projection_outbox SET state = 'RETRY_PENDING' "
                "WHERE mutation_id = ? AND projection_name = 'VECTOR'",
                (env.mutation_id,),
            )
            await db.execute(
                "UPDATE v4_assertions SET jurisdiction = 'outside' WHERE mutation_id = ?",
                (env.mutation_id,),
            )
            await db.commit()
        response = await env.client.post(
            "/v4/memory/search",
            json={
                "session_id": env.session_id,
                "query": "durable memory",
                "jurisdiction": "inside",
            },
        )
        assert response.status_code == 200, response.text
        assert "degraded_sources" in response.json()
        assert response.json()["degraded_sources"] == []
        assert response.json()["results"] == []
