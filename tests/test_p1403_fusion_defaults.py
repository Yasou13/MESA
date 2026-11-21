"""P14-03 (D64): fusion defaults (assertion weight 0.0, graph off by default)."""

from __future__ import annotations

import kuzu
import pytest

from mesa_memory.api import server
from mesa_memory.config import (
    MesaConfig,
    RuntimeProfile,
    RuntimeProfileConfig,
    load_runtime_profile,
)
from tests.test_model_optional_runtime import (
    _assert_committed,
    _consume,
    _insert,
    _lanes,
    _search,
)
from tests.test_model_optional_runtime import model_runtime as model_runtime


def test_default_config_sets_assertion_weight_zero_and_graph_off(tmp_path, monkeypatch):
    monkeypatch.delenv("MESA_RRF_ASSERTION_WEIGHT", raising=False)
    monkeypatch.delenv("MESA_GRAPH_ENABLED", raising=False)
    monkeypatch.delenv("MESA_LEGAL_RULES_ENABLED", raising=False)

    cfg = MesaConfig(_env_file=None)
    assert cfg.rrf_assertion_weight == 0.0
    assert cfg.rrf_vector_weight == 8.0
    assert cfg.rrf_bm25_weight == 1.0
    assert cfg.rrf_graph_weight == 1.0
    assert cfg.legal_rules_enabled is True

    runtime = load_runtime_profile(
        {
            "MESA_RUNTIME_PROFILE": "combined",
            "MESA_STORAGE_ROOT": str(tmp_path / "data"),
        }
    )
    assert runtime.graph_enabled is False

    direct = RuntimeProfileConfig(
        profile=RuntimeProfile.COMBINED,
        storage_root=tmp_path / "data",
        load_dotenv=False,
        dotenv_path=None,
        model_enabled=False,
        external_provider_enabled=False,
        api_enabled=True,
        worker_enabled=True,
        require_worker_readiness=False,
    )
    assert direct.graph_enabled is False


@pytest.mark.asyncio
async def test_default_runtime_never_opens_or_creates_kuzu_and_excludes_assertion_from_rrf(
    model_runtime, monkeypatch
):
    monkeypatch.delenv("MESA_GRAPH_ENABLED", raising=False)
    monkeypatch.delenv("MESA_RRF_ASSERTION_WEIGHT", raising=False)

    def _forbid_kuzu(*args, **kwargs):
        raise AssertionError("Kuzu must not be opened with default settings")

    monkeypatch.setattr(kuzu, "Database", _forbid_kuzu)

    async with model_runtime() as env:
        assert server.state.runtime_profile.graph_enabled is False
        assert server.state.graph_provider is None
        storage_root = server.state.runtime_profile.storage_root

        await _insert(env)
        await _consume(env, 3)
        await _assert_committed(env)

        lanes = await _lanes(env)
        assert lanes == {"SQL": "COMPLETED", "VECTOR": "SKIPPED", "GRAPH": "SKIPPED"}

        result = await _search(env)
        assert result["results"]
        assert "graph" in result["degraded_sources"]
        top = result["results"][0]
        bm25_rank = top["retrieval_provenance"]["lane_ranks"]["bm25"]
        expected_rrf = 1.0 / (60 + bm25_rank)
        assert top["rrf_score"] == pytest.approx(expected_rrf)

        kuzu_artifacts = [
            p.name for p in storage_root.iterdir() if "kuzu" in p.name.lower()
        ]
        assert kuzu_artifacts == []


@pytest.mark.asyncio
async def test_explicit_env_restores_graph_and_assertion_weight(model_runtime):
    async with model_runtime(
        MESA_GRAPH_ENABLED=True, MESA_RRF_ASSERTION_WEIGHT="1.0"
    ) as env:
        assert server.state.runtime_profile.graph_enabled is True
        assert server.state.graph_provider is not None

        await _insert(env)
        await _consume(env, 3)
        await _assert_committed(env)

        lanes = await _lanes(env)
        assert lanes["GRAPH"] == "COMPLETED"
        result = await _search(env)
        assert result["results"]
        assert "graph" not in result["degraded_sources"]
        top = result["results"][0]
        ranks = top["retrieval_provenance"]["lane_ranks"]
        assert "assertion" in ranks
        expected_rrf = sum(1.0 / (60 + rank) for rank in ranks.values())
        assert top["rrf_score"] == pytest.approx(expected_rrf)


@pytest.mark.parametrize(("env_value", "expected"), [(None, False), ("true", True)])
def test_rebuild_provider_manifest_follows_graph_default(
    monkeypatch, env_value, expected
):
    from mesa_memory import rebuild_runner

    monkeypatch.setenv("MESA_EMBEDDING_ENABLED", "false")
    if env_value is None:
        monkeypatch.delenv("MESA_GRAPH_ENABLED", raising=False)
    else:
        monkeypatch.setenv("MESA_GRAPH_ENABLED", env_value)

    providers = rebuild_runner._provider_runtime()
    assert providers.manifest["graph_enabled"] is expected
