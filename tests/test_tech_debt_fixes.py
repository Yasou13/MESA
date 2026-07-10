"""
Tests for tech-debt fixes:
  - Config validation with new fields
  - Client adapter imports (Zep, Letta)
  - Multi-model judge evaluator
  - Agreement computation
  - Statistics module
  - LoCoMo external loader
  - Reporter with agreement data
"""

import json
import math
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure repo root is on path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "mesa-benchmark"))


# ===================================================================
# TEST 1: Config loads new fields correctly
# ===================================================================


class TestConfigNewFields:
    def test_config_loads_multi_judge_models(self, tmp_path):
        from mesa_benchmark.core.config import load_config

        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text(
            """
suite_name: "Test Suite"
iterations: 5
seed: 42
dataset:
  name: "test"
  version: "v1"
  path: "test.json"
client:
  name: "dummy"
  adapter_class: "mesa_benchmark.clients.dummy_client.DummyClientAdapter"
  timeout_ms: 5000
  parameters: {}
evaluation:
  metrics: ["hit_at_k"]
  llm_judge_model: "gpt-4o-mini"
  multi_judge_models:
    - "gpt-4o-mini"
    - "claude-sonnet-4-20250514"
  enable_agreement: true
""",
            encoding="utf-8",
        )

        config = load_config(config_yaml)
        assert config.evaluation.llm_judge_model == "gpt-4o-mini"
        assert len(config.evaluation.multi_judge_models) == 2
        assert "claude-sonnet-4-20250514" in config.evaluation.multi_judge_models
        assert config.evaluation.enable_agreement is True
        assert config.iterations == 5

    def test_config_defaults_for_new_fields(self, tmp_path):
        from mesa_benchmark.core.config import load_config

        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text(
            """
suite_name: "Minimal"
iterations: 1
seed: 42
dataset:
  name: "test"
  path: "test.json"
client:
  name: "dummy"
  adapter_class: "mesa_benchmark.clients.dummy_client.DummyClientAdapter"
evaluation:
  metrics: ["hit_at_k"]
""",
            encoding="utf-8",
        )

        config = load_config(config_yaml)
        assert config.evaluation.multi_judge_models == []
        assert config.evaluation.enable_agreement is False
        assert config.evaluation.llm_judge_model is None


# ===================================================================
# TEST 2: Zep and Letta client imports
# ===================================================================


class TestClientImports:
    def test_zep_client_class_exists(self):
        from mesa_benchmark.clients.zep_client import ZepClientAdapter

        adapter = ZepClientAdapter()
        assert hasattr(adapter, "initialize")
        assert hasattr(adapter, "clear_memory")
        assert hasattr(adapter, "add_memory")
        assert hasattr(adapter, "answer")
        assert hasattr(adapter, "close")

    def test_letta_client_class_exists(self):
        from mesa_benchmark.clients.letta_client import LettaClientAdapter

        adapter = LettaClientAdapter()
        assert hasattr(adapter, "initialize")
        assert hasattr(adapter, "clear_memory")
        assert hasattr(adapter, "add_memory")
        assert hasattr(adapter, "answer")
        assert hasattr(adapter, "close")

    def test_clients_init_exports(self):
        from mesa_benchmark.clients import __all__

        assert "ZepClientAdapter" in __all__
        assert "LettaClientAdapter" in __all__


# ===================================================================
# TEST 3: Multi-model judge evaluator
# ===================================================================


class TestMultiModelJudge:
    def test_multi_model_judge_import(self):
        from mesa_benchmark.evaluators.multi_model_judge import (
            MultiModelJudgeEvaluator,
        )

        evaluator = MultiModelJudgeEvaluator(
            judge_models=["gpt-4o-mini", "claude-sonnet-4-20250514"]
        )
        assert evaluator.judge_models == ["gpt-4o-mini", "claude-sonnet-4-20250514"]

    def test_pairwise_agreement_all_agree(self):
        from mesa_benchmark.evaluators.multi_model_judge import (
            MultiModelJudgeEvaluator,
        )

        agreement = MultiModelJudgeEvaluator._compute_pairwise_agreement(
            [True, True, True]
        )
        assert agreement == 1.0

    def test_pairwise_agreement_none_agree(self):
        from mesa_benchmark.evaluators.multi_model_judge import (
            MultiModelJudgeEvaluator,
        )

        agreement = MultiModelJudgeEvaluator._compute_pairwise_agreement(
            [True, False]
        )
        assert agreement == 0.0

    def test_pairwise_agreement_partial(self):
        from mesa_benchmark.evaluators.multi_model_judge import (
            MultiModelJudgeEvaluator,
        )

        # 3 verdicts: True, True, False → 2 agree out of 3 pairs
        agreement = MultiModelJudgeEvaluator._compute_pairwise_agreement(
            [True, True, False]
        )
        # Pairs: (T,T)=agree, (T,F)=disagree, (T,F)=disagree → 1/3
        assert abs(agreement - (1 / 3)) < 0.01

    def test_multi_model_judge_fallback_on_all_failures(self):
        """When all LLM calls fail, judge should fall back to substring match."""
        from mesa_benchmark.evaluators.multi_model_judge import (
            MultiModelJudgeEvaluator,
        )
        from mesa_benchmark.clients.base import BenchmarkResponse
        from mesa_benchmark.datasets.schemas import BenchmarkQuestion

        evaluator = MultiModelJudgeEvaluator(judge_models=["nonexistent-model"])

        question = BenchmarkQuestion(
            id="test_q",
            query="What is X?",
            ground_truth="the answer is Y",
            expected_context_ids=["ctx1"],
            evaluation_strategy="multi_model_judge",
        )
        response = BenchmarkResponse(
            answer_text="the answer is Y for sure",
            retrieved_context_ids=["ctx1"],
            latency_ms=50.0,
        )

        result = evaluator.evaluate(response, question)
        # Fallback should match via substring
        assert result.is_correct is True
        assert result.metadata.get("fallback") is True


# ===================================================================
# TEST 4: Agreement computation
# ===================================================================


class TestAgreementComputation:
    def test_perfect_agreement(self):
        from mesa_benchmark.evaluators.agreement import compute_agreement

        result = compute_agreement([1.0, 0.0, 1.0], [1.0, 0.0, 1.0])
        assert result["agreement_rate"] == 100.0
        assert result["cohens_kappa"] == 1.0

    def test_no_agreement(self):
        from mesa_benchmark.evaluators.agreement import compute_agreement

        result = compute_agreement([1.0, 1.0, 1.0], [0.0, 0.0, 0.0])
        assert result["agreement_rate"] == 0.0

    def test_partial_agreement(self):
        from mesa_benchmark.evaluators.agreement import compute_agreement

        result = compute_agreement([1.0, 0.0, 1.0, 0.0], [1.0, 0.0, 0.0, 1.0])
        assert result["agreement_rate"] == 50.0
        assert result["total"] == 4

    def test_empty_lists(self):
        from mesa_benchmark.evaluators.agreement import compute_agreement

        result = compute_agreement([], [])
        assert result["agreement_rate"] == 0.0
        assert result["total"] == 0


# ===================================================================
# TEST 5: Statistics module
# ===================================================================


class TestStatistics:
    def test_run_statistics(self):
        from mesa_benchmark.reports.statistics import compute_run_statistics

        stats = compute_run_statistics([90.0, 91.0, 92.0, 89.0, 90.5])
        assert stats["n"] == 5
        assert abs(stats["mean"] - 90.5) < 0.01
        assert stats["std"] > 0

    def test_t_test_significant(self):
        from mesa_benchmark.reports.statistics import compute_t_test_p_value

        # Very different samples → should be significant
        result = compute_t_test_p_value(
            [90, 91, 92, 90, 91], [50, 51, 52, 50, 51]
        )
        assert result["is_significant"] is True
        assert result["p_value_approx"] < 0.05

    def test_t_test_not_significant(self):
        from mesa_benchmark.reports.statistics import compute_t_test_p_value

        # Very similar samples → should NOT be significant
        result = compute_t_test_p_value(
            [90.0, 90.1, 89.9], [90.0, 90.0, 90.0]
        )
        assert result["is_significant"] is False

    def test_t_test_insufficient_samples(self):
        from mesa_benchmark.reports.statistics import compute_t_test_p_value

        result = compute_t_test_p_value([90.0], [50.0])
        assert result["p_value_approx"] == 1.0
        assert result["is_significant"] is False


# ===================================================================
# TEST 6: LoCoMo external loader
# ===================================================================


class TestLoCoMoLoader:
    def test_load_locomo_format_basic(self, tmp_path):
        from mesa_benchmark.datasets.external_loader import ExternalDatasetLoader

        mock_data = [
            {
                "id": "locomo_001",
                "title": "Test Conversation",
                "category": "multi_hop",
                "conversation": [
                    {"id": "turn_1", "text": "Alice works at TechCorp.", "speaker": "user"},
                    {"id": "turn_2", "text": "Bob visited Paris last week.", "speaker": "assistant"},
                ],
                "qa_pairs": [
                    {
                        "id": "q1",
                        "question": "Where does Alice work?",
                        "answer": "TechCorp",
                        "supporting_facts": ["turn_1"],
                    }
                ],
            }
        ]

        path = tmp_path / "locomo_test.json"
        path.write_text(json.dumps(mock_data), encoding="utf-8")

        scenarios = ExternalDatasetLoader.load_locomo_format(path)
        assert len(scenarios) == 1
        assert scenarios[0].id == "locomo_001"
        assert len(scenarios[0].contexts) == 2
        assert len(scenarios[0].questions) == 1


# ===================================================================
# TEST 7: Reporter with agreement data
# ===================================================================


class TestReporter:
    def test_reporter_generates_agreement_section(self):
        from mesa_benchmark.reports.reporter import MarkdownReporter

        config = MagicMock()
        config.suite_name = "Test Suite"

        reporter = MarkdownReporter("test_run_123", config)

        metrics_dict = {
            "total_questions": 100,
            "correct_answers": 85,
            "accuracy": 0.85,
            "avg_latency_ms": 42.5,
            "p95_latency_ms": 80.0,
            "p99_latency_ms": 120.0,
            "hit_at_1": 0.75,
            "hit_at_3": 0.88,
            "hit_at_5": 0.92,
            "mrr": 0.80,
            "agreement": {
                "agreement_rate": 87.5,
                "cohens_kappa": 0.72,
                "concordant": 35,
                "total": 40,
                "contingency_table": {
                    "both_correct": 30,
                    "only_a_correct": 3,
                    "only_b_correct": 2,
                    "both_incorrect": 5,
                },
            },
        }

        report_path = reporter.generate_report_from_dict(metrics_dict)
        assert Path(report_path).exists()

        content = Path(report_path).read_text(encoding="utf-8")
        assert "Agreement Rate" in content
        assert "Cohen's Kappa" in content
        assert "87.50" in content
        assert "Contingency Table" in content

        # Cleanup
        Path(report_path).unlink(missing_ok=True)


# ===================================================================
# TEST 8: Dockerfile uses lock file
# ===================================================================


class TestDockerfile:
    def test_dockerfile_uses_lock_file(self):
        dockerfile_path = REPO_ROOT / "mesa-benchmark" / "Dockerfile"
        content = dockerfile_path.read_text(encoding="utf-8")
        assert "requirements-lock.txt" in content
        assert "requirements.txt" not in content or "requirements-lock.txt" in content


# ===================================================================
# TEST 9: Config uses 200 dataset
# ===================================================================


class TestConfigFile:
    def test_config_uses_200_dataset(self):
        config_path = REPO_ROOT / "mesa-benchmark" / "config.yaml"
        content = config_path.read_text(encoding="utf-8")
        assert "comprehensive_200_dataset.json" in content

    def test_config_has_llm_judge_enabled(self):
        config_path = REPO_ROOT / "mesa-benchmark" / "config.yaml"
        content = config_path.read_text(encoding="utf-8")
        assert "gpt-4o-mini" in content

    def test_config_has_5_iterations(self):
        from mesa_benchmark.core.config import load_config

        config = load_config(REPO_ROOT / "mesa-benchmark" / "config.yaml")
        assert config.iterations == 5


# ===================================================================
# TEST 10: No fake reproducibility report
# ===================================================================


class TestReproducibilityReport:
    def test_fake_report_deleted(self):
        report_path = REPO_ROOT / "reproducibility_report.json"
        if report_path.exists():
            import json
            data = json.loads(report_path.read_text(encoding="utf-8"))
            assert "accuracy_statistics" in data and "seeds_run" in data, (
                "reproducibility_report.json exists but is not a valid real multi-seed report!"
            )

    def test_reproduce_script_no_dry_run(self):
        script_path = REPO_ROOT / "scripts" / "reproduce_benchmark.py"
        content = script_path.read_text(encoding="utf-8")
        assert "dry_run" not in content, (
            "reproduce_benchmark.py still contains dry-run logic with fake data!"
        )
        assert "91.5" not in content, (
            "reproduce_benchmark.py still contains hardcoded fake accuracy values!"
        )
