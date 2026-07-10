"""
Unit and integration tests for MESA benchmark enhancements:
  - Agreement rate calculation
  - Multi-seed statistics & t-test
  - External LoCoMo loader
  - MesaClientAdapter KùzuDB graph architecture
"""

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "mesa-benchmark"))

import pytest

from mesa_benchmark.clients.mesa_client import MesaClientAdapter
from mesa_benchmark.datasets.external_loader import ExternalDatasetLoader
from mesa_benchmark.datasets.schemas import BenchmarkQuestion, MemoryContext
from mesa_benchmark.evaluators.agreement import compute_agreement
from mesa_benchmark.reports.statistics import (
    compute_run_statistics,
    compute_t_test_p_value,
)


def test_compute_agreement():
    scores_a = [1.0, 1.0, 0.0, 0.0]
    scores_b = [1.0, 1.0, 0.0, 1.0]

    res = compute_agreement(scores_a, scores_b)
    assert res["total"] == 4
    assert res["concordant"] == 3
    assert res["agreement_rate"] == 75.0
    assert "cohens_kappa" in res


def test_compute_statistics():
    runs = [90.0, 92.0, 94.0]
    stats = compute_run_statistics(runs)
    assert stats["mean"] == 92.0
    assert stats["std"] > 0.0
    assert stats["n"] == 3
    assert "±" in stats["formatted_str"]

    t_res = compute_t_test_p_value([90.0, 92.0, 94.0], [80.0, 82.0, 84.0])
    assert t_res["is_significant"] is True
    assert t_res["mean_diff"] == 10.0


def test_external_loader_locomo(tmp_path):
    mock_locomo = [
        {
            "id": "locomo_test_1",
            "title": "Test Dialogue",
            "context": [
                {"id": "c1", "text": "Alice lives in Zurich."},
                {"id": "c2", "text": "Bob works with Alice."},
            ],
            "qa_pairs": [
                {
                    "id": "q1",
                    "question": "Where does Bob's colleague live?",
                    "answer": "Zurich",
                    "supporting_facts": ["c1", "c2"],
                }
            ],
        }
    ]

    p = tmp_path / "mock_locomo.json"
    p.write_text(json.dumps(mock_locomo), encoding="utf-8")

    scenarios = ExternalDatasetLoader.load_locomo_format(p)
    assert len(scenarios) == 1
    assert scenarios[0].id == "locomo_test_1"
    assert len(scenarios[0].contexts) == 2
    assert len(scenarios[0].questions) == 1


def test_mesa_client_adapter_graph_metadata():
    adapter = MesaClientAdapter()
    adapter.initialize({"verbose": True})

    try:
        ctx = MemoryContext(
            id="test_entity_1",
            text="Dr. Elena Vance leads Project Omega at MESA Corp.",
            metadata={"relations": [{"target": "Project Omega", "type": "LEADS"}]},
        )
        add_res = adapter.add_memory(ctx)
        assert "latency_ms" in add_res

        question = BenchmarkQuestion(
            id="q_1",
            query="Who leads Project Omega?",
            ground_truth="Dr. Elena Vance",
            expected_context_ids=["test_entity_1"],
        )
        response = adapter.answer(question)
        assert response.metadata.get("multi_hop_enabled") is True
        assert response.metadata.get("graph_backend") == "KuzuDB"
    finally:
        adapter.close()
