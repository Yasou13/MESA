"""The runtime default must follow the recorded DEV-only selection rule."""

import json
from pathlib import Path

from mesa_memory.config import MesaConfig


def test_recorded_dev_matrix_covers_all_requested_weights_and_modes():
    data = json.loads((Path(__file__).parents[1] / "docs/benchmarks/round14_dev.json").read_text())
    assert data["split"] == "dev"
    assert data["other_lane_weights"] == {"bm25": 1, "assertion": 1, "graph": 1}
    assert (data["document_count"], data["chunk_count"]) == (141, 5041)
    assert {(r["vector_weight"], r["rerank_enabled"]) for r in data["runs"]} == {
        (weight, mode) for weight in (1, 2, 4, 8) for mode in (False, True)
    }
    assert len(data["runs"]) == 8
    for run in data["runs"]:
        assert run["metrics"]["count_total"] == 101
        assert run["metrics"]["count_answerable"] == 85
        assert 0 <= run["passage_mrr_unrounded"] <= 1
        assert round(run["passage_mrr_unrounded"], 4) == run["metrics"]["passage_mrr"]


def test_default_uses_best_disabled_dev_mrr_only_with_required_gain(monkeypatch):
    data = json.loads((Path(__file__).parents[1] / "docs/benchmarks/round14_dev.json").read_text())
    disabled = [r for r in data["runs"] if not r["rerank_enabled"]]
    baseline = next(r for r in disabled if r["vector_weight"] == 1)
    best = max(disabled, key=lambda r: (r["passage_mrr_unrounded"], -r["vector_weight"]))
    gain = best["passage_mrr_unrounded"] - baseline["passage_mrr_unrounded"]
    expected = best["vector_weight"] if gain >= 0.02 else 1
    monkeypatch.delenv("MESA_RRF_VECTOR_WEIGHT", raising=False)
    assert MesaConfig(_env_file=None).rrf_vector_weight == expected
    assert data["selected_default_vector_weight"] == expected
