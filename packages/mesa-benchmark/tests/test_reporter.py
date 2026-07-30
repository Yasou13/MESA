import tempfile
from pathlib import Path

from mesa_benchmark.core.config import (
    BenchmarkConfig,
    ClientConfig,
    DatasetConfig,
    EvaluationConfig,
)
from mesa_benchmark.reports.reporter import MarkdownReporter


def test_reporter_formatting() -> None:
    config = BenchmarkConfig(
        suite_name="Test Suite",
        iterations=1,
        seed=42,
        dataset=DatasetConfig(name="test", path="test.json"),
        client=ClientConfig(name="test", adapter_class="test"),
        evaluation=EvaluationConfig(metrics=["hit_at_k"]),
    )

    reporter = MarkdownReporter(run_id="run_123", config=config)

    metrics_dict = {
        "total_questions": 100,
        "correct_answers": 80,
        "accuracy": 0.8,
        "deterministic_evaluable_questions": 60,
        "deterministic_correct_answers": 54,
        "deterministic_accuracy": 0.9,
        "semantic_judge_evaluable_questions": 40,
        "semantic_judge_correct_answers": 26,
        "semantic_judge_accuracy": 0.65,
        "semantic_judge_avg_score": 0.7,
        "hit_at_1": 0.9,
        "agreement": {
            "agreement_rate": 85.0,
            "cohens_kappa": 0.75,
            "contingency_table": {
                "both_correct": 80,
                "both_incorrect": 5,
                "only_a_correct": 10,
                "only_b_correct": 5,
            },
        },
    }

    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "report.md"
        reporter.generate_report_from_dict(metrics_dict, output_path=str(out_path))

        assert out_path.exists()
        content = out_path.read_text()

        assert "Test Suite" in content
        assert "run_123" in content
        assert "80" in content
        assert "%80.00" in content
        assert "%85.00" in content
        assert "0.7500" in content
        assert "Contingency Table" in content
        assert "Overall Primary-Evaluator Accuracy" in content
        assert "Deterministic Accuracy" in content
        assert "Semantic Judge Accuracy" in content
        assert "%90.00 (54/60)" in content
        assert "%65.00 (26/40)" in content


def test_reporter_marks_semantic_judge_metrics_na_when_absent() -> None:
    config = BenchmarkConfig(
        suite_name="Test Suite",
        iterations=1,
        seed=42,
        dataset=DatasetConfig(name="test", path="test.json"),
        client=ClientConfig(name="test", adapter_class="test"),
        evaluation=EvaluationConfig(metrics=[]),
    )
    reporter = MarkdownReporter(run_id="run_123", config=config)

    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "report.md"
        reporter.generate_report_from_dict(
            {"total_questions": 1, "correct_answers": 1, "accuracy": 1.0},
            output_path=str(out_path),
        )

        assert "| **Semantic Judge Accuracy** | N/A |" in out_path.read_text()
