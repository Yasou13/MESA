"""P14-03 (D64) & D79: fusion defaults (assertion weight 0.0, no graph lane)."""

from __future__ import annotations

import pytest

from mesa_memory.api import server
from mesa_memory.config import MesaConfig
from tests.test_model_optional_runtime import (
    _assert_committed,
    _consume,
    _insert,
    _lanes,
    _search,
)
from tests.test_model_optional_runtime import model_runtime as model_runtime


def test_default_config_sets_assertion_weight_zero(monkeypatch):
    monkeypatch.delenv("MESA_RRF_ASSERTION_WEIGHT", raising=False)
    monkeypatch.delenv("MESA_LEGAL_RULES_ENABLED", raising=False)

    cfg = MesaConfig(_env_file=None)
    assert cfg.rrf_assertion_weight == 0.0
    assert cfg.rrf_vector_weight == 8.0
    assert cfg.rrf_bm25_weight == 1.0
    assert cfg.legal_rules_enabled is True


@pytest.mark.asyncio
async def test_default_runtime_has_no_graph_lane_and_excludes_assertion_from_rrf(
    model_runtime, monkeypatch
):
    monkeypatch.delenv("MESA_RRF_ASSERTION_WEIGHT", raising=False)

    async with model_runtime() as env:
        storage_root = server.state.runtime_profile.storage_root

        await _insert(env)
        await _consume(env, 2)
        await _assert_committed(env)

        lanes = await _lanes(env)
        assert lanes == {"SQL": "COMPLETED", "VECTOR": "SKIPPED"}

        result = await _search(env)
        assert result["results"]
        assert "graph" not in result["degraded_sources"]
        top = result["results"][0]
        bm25_rank = top["retrieval_provenance"]["lane_ranks"]["bm25"]
        expected_rrf = 1.0 / (60 + bm25_rank)
        assert top["rrf_score"] == pytest.approx(expected_rrf)
        assert not (storage_root / "graph").exists()


@pytest.mark.asyncio
async def test_explicit_env_restores_assertion_weight(model_runtime):
    async with model_runtime(MESA_RRF_ASSERTION_WEIGHT="1.0") as env:
        await _insert(env)
        await _consume(env, 2)
        await _assert_committed(env)

        lanes = await _lanes(env)
        assert lanes == {"SQL": "COMPLETED", "VECTOR": "SKIPPED"}
        result = await _search(env)
        assert result["results"]
        top = result["results"][0]
        ranks = top["retrieval_provenance"]["lane_ranks"]
        assert "assertion" in ranks
        expected_rrf = sum(1.0 / (60 + rank) for rank in ranks.values())
        assert top["rrf_score"] == pytest.approx(expected_rrf)
