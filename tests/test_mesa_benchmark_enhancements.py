# ruff: noqa: E402
"""
Unit and integration tests for MESA benchmark enhancements:
  - Agreement rate calculation
  - Multi-seed statistics & t-test
  - External LoCoMo loader
  - MesaClientAdapter KùzuDB graph architecture
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "mesa-benchmark"))


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
        assert response.metadata.get("mesa_version") == "0.6.0"
    finally:
        adapter.close()


def test_mesa_client_adapter_rerank_config():
    adapter = MesaClientAdapter()
    adapter.initialize(
        {
            "verbose": True,
            "enable_rerank": True,
            "reranker_model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
            "top_n": 10,
        }
    )

    try:
        assert adapter.enable_rerank is True
        assert adapter.top_n == 10
        assert adapter.retriever is not None
        assert adapter.retriever.reranker is not None

        question = BenchmarkQuestion(
            id="q_rerank_1",
            query="Testing CrossEncoder reranker inside benchmark client",
            ground_truth="Test",
            expected_context_ids=[],
        )
        response = adapter.answer(question)
        assert response.metadata.get("rerank_enabled") is True
        assert response.metadata.get("mesa_version") == "0.6.0"
    finally:
        adapter.close()


def test_bottleneck_diagnostics_and_report(tmp_path):
    import json
    from types import SimpleNamespace

    from mesa_benchmark.metrics.calculator import calculate_metrics_from_jsonl
    from mesa_benchmark.reports.reporter import MarkdownReporter

    # Create dummy JSONL with diagnostics
    jsonl_path = tmp_path / "test_diag.jsonl"
    records = [
        {
            "run_id": "test_run",
            "iteration": 1,
            "scenario_id": "sc1",
            "question_id": "q1",
            "score": 0.0,
            "is_correct": False,
            "latency_ms": 500.0,
            "expected_context_ids": ["c1"],
            "retrieved_context_ids": ["c2"],
            "failure_attribution": "RETRIEVAL_MISS",
            "latency_breakdown_ms": {
                "vector_and_graph_search_ms": 20.0,
                "rerank_ms": 450.0,
                "total_retrieval_ms": 480.0,
            },
        },
        {
            "run_id": "test_run",
            "iteration": 1,
            "scenario_id": "sc1",
            "question_id": "q2",
            "score": 1.0,
            "is_correct": True,
            "latency_ms": 200.0,
            "expected_context_ids": ["c3"],
            "retrieved_context_ids": ["c3"],
            "failure_attribution": "SUCCESS",
            "latency_breakdown_ms": {
                "vector_and_graph_search_ms": 15.0,
                "rerank_ms": 150.0,
                "total_retrieval_ms": 170.0,
            },
        },
    ]

    with open(jsonl_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    metrics = calculate_metrics_from_jsonl(jsonl_path)
    assert metrics.failure_attributions.get("RETRIEVAL_MISS") == 1
    assert metrics.avg_latency_breakdown_ms.get("rerank_ms") == 300.0

    dummy_config = SimpleNamespace(
        suite_name="test_suite",
        evaluation=SimpleNamespace(
            metrics=["latency", "hit_at_k"], enable_agreement=False
        ),
    )
    reporter = MarkdownReporter("test_run", dummy_config, output_dir=str(tmp_path))
    report_file = reporter.generate_report(metrics)

    with open(report_file, "r", encoding="utf-8") as rf:
        content = rf.read()
        assert "## 🛠️ 5. Root-Cause & Bottleneck Diagnostics" in content
        assert "RETRIEVAL_MISS" in content
        assert "rerank_ms" in content
