from mesa_benchmark.clients.base import BenchmarkResponse
from mesa_benchmark.datasets.schemas import BenchmarkQuestion
from mesa_benchmark.evaluators.agreement import compute_agreement
from mesa_benchmark.evaluators.exact_match import ExactMatchEvaluator


def test_exact_match_evaluator_correct() -> None:
    evaluator = ExactMatchEvaluator()
    q = BenchmarkQuestion(
        id="q1", query="test?", ground_truth="Paris", evaluation_strategy="exact_match"
    )
    r = BenchmarkResponse(
        answer_text="The capital of France is Paris.",
        retrieved_context_ids=["c1"],
        latency_ms=100.0,
        token_usage={},
    )
    res = evaluator.evaluate(r, q)
    assert res.is_correct is True
    assert res.score == 1.0


def test_exact_match_evaluator_incorrect() -> None:
    evaluator = ExactMatchEvaluator()
    q = BenchmarkQuestion(
        id="q1", query="test?", ground_truth="Paris", evaluation_strategy="exact_match"
    )
    r = BenchmarkResponse(
        answer_text="The capital of France is Lyon.",
        retrieved_context_ids=["c1"],
        latency_ms=100.0,
        token_usage={},
    )
    res = evaluator.evaluate(r, q)
    assert res.is_correct is False
    assert res.score == 0.0


def test_compute_agreement_perfect() -> None:
    scores_a = [1.0, 1.0, 0.0, 0.0]
    scores_b = [1.0, 1.0, 0.0, 0.0]
    res = compute_agreement(scores_a, scores_b)
    assert res["agreement_rate"] == 100.0
    assert res["cohens_kappa"] == 1.0
    assert res["contingency_table"]["both_correct"] == 2
    assert res["contingency_table"]["both_incorrect"] == 2


def test_compute_agreement_none() -> None:
    scores_a = [1.0, 1.0, 0.0, 0.0]
    scores_b = [0.0, 0.0, 1.0, 1.0]
    res = compute_agreement(scores_a, scores_b)
    assert res["agreement_rate"] == 0.0
    assert res["cohens_kappa"] < 0.0
