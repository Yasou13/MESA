import json
import math
import statistics
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class BenchmarkMetrics(BaseModel):
    """Aggregate metrics for a benchmark run."""

    total_questions: int = Field(0, description="Total number of evaluated questions.")
    correct_answers: int = Field(
        0, description="Number of questions answered correctly."
    )
    accuracy: float = Field(0.0, description="Overall accuracy (0.0 to 1.0).")
    avg_latency_ms: float = Field(0.0, description="Average response latency in ms.")
    p95_latency_ms: Optional[float] = Field(
        None, description="95th percentile response latency in ms; null when n < 20."
    )
    p99_latency_ms: Optional[float] = Field(
        None, description="99th percentile response latency in ms; null when n < 20."
    )
    avg_score: float = Field(
        0.0, description="Average score (useful for partial scoring)."
    )
    hit_at_1: float = Field(
        0.0,
        description="Hit@1: fraction of queries where ground truth is in top-1 result.",
    )
    hit_at_3: float = Field(
        0.0,
        description="Hit@3: fraction of queries where ground truth is in top-3 results.",
    )
    hit_at_5: float = Field(
        0.0,
        description="Hit@5: fraction of queries where ground truth is in top-5 results.",
    )
    mrr: float = Field(0.0, description="Mean Reciprocal Rank.")
    ndcg: float = Field(0.0, description="Normalized Discounted Cumulative Gain.")
    token_efficiency: Optional[float] = Field(
        None, description="Total prompt tokens / correct answers."
    )
    failure_attributions: Dict[str, int] = Field(
        default_factory=dict,
        description="Breakdown of failure causes: RETRIEVAL_MISS, CONTEXT_NOISE, LLM_REASONING_ERROR, TIMEOUT_OR_ERROR.",
    )
    avg_latency_breakdown_ms: Dict[str, float] = Field(
        default_factory=dict,
        description="Average response latency across each internal retrieval stage in ms.",
    )
    latency_sample_size: int = 0
    retrieval_evaluable_questions: int = 0
    avg_generation_latency_ms: Optional[float] = None
    answer_exact_match: Optional[float] = None
    answer_token_f1: Optional[float] = None
    deterministic_evaluable_questions: int = 0
    deterministic_correct_answers: int = 0
    deterministic_accuracy: Optional[float] = None
    semantic_judge_evaluable_questions: int = 0
    semantic_judge_correct_answers: int = 0
    semantic_judge_accuracy: Optional[float] = None
    semantic_judge_avg_score: Optional[float] = None
    infrastructure_errors: int = 0
    valid: bool = True


class MetricsEngine:
    """Stateless engine for computing retrieval and performance metrics."""

    @staticmethod
    def calculate_hit_at_k(
        expected_ids: List[str], retrieved_ids: List[str], k: int
    ) -> int:
        """Returns 1 if any expected ID is found in the top-k retrieved IDs, else 0."""
        top_k = retrieved_ids[:k]
        for eid in expected_ids:
            if eid in top_k:
                return 1
        return 0

    @staticmethod
    def calculate_reciprocal_rank(
        expected_ids: List[str], retrieved_ids: List[str]
    ) -> float:
        """Returns the reciprocal rank (1/rank) of the first relevant result."""
        for i, rid in enumerate(retrieved_ids):
            if rid in expected_ids:
                return 1.0 / (i + 1)
        return 0.0

    @staticmethod
    def calculate_mrr(ranks: List[int]) -> float:
        """
        Calculates Mean Reciprocal Rank from a list of ranks.
        A rank of 0 means "not found" and contributes 0.0.
        """
        if not ranks:
            return 0.0
        total = 0.0
        for rank in ranks:
            if rank > 0:
                total += 1.0 / rank
        return total / len(ranks)

    @staticmethod
    def calculate_ndcg(
        expected_ids: List[str], retrieved_ids: List[str], k: int = 5
    ) -> float:
        """
        Calculates nDCG@k using expected context bounds for ideal DCG calculation.
        """
        if not expected_ids:
            return 0.0

        relevance_scores = [1.0 if r in expected_ids else 0.0 for r in retrieved_ids]

        def dcg(scores: List[float], k: int) -> float:
            val = 0.0
            for i, rel in enumerate(scores[:k]):
                val += rel / math.log2(i + 2)  # i+2 because log2(1) = 0
            return val

        actual_dcg = dcg(relevance_scores, k)

        # Ideal scenario: all expected contexts are retrieved at the top
        ideal_scores = [1.0] * min(k, len(expected_ids)) + [0.0] * max(
            0, k - len(expected_ids)
        )
        ideal_dcg = dcg(ideal_scores, k)

        if ideal_dcg == 0:
            return 0.0
        return actual_dcg / ideal_dcg

    @staticmethod
    def welch_t_test(scores_a: List[float], scores_b: List[float]) -> Dict[str, float]:
        """
        Performs Welch's t-test between two sets of scores.
        Returns t-statistic and approximate p-value.
        """
        n_a, n_b = len(scores_a), len(scores_b)
        if n_a < 2 or n_b < 2:
            return {"t_statistic": 0.0, "p_value": 1.0, "significant": False}

        mean_a = statistics.mean(scores_a)
        mean_b = statistics.mean(scores_b)
        var_a = statistics.variance(scores_a)
        var_b = statistics.variance(scores_b)

        se = math.sqrt(var_a / n_a + var_b / n_b)
        if se == 0:
            if mean_a == mean_b:
                return {"t_statistic": 0.0, "p_value": 1.0, "significant": False}
            else:
                return {
                    "t_statistic": float("inf") if mean_a > mean_b else float("-inf"),
                    "p_value": 0.0,
                    "significant": True,
                }

        t_stat = (mean_a - mean_b) / se

        # Welch-Satterthwaite degrees of freedom
        # Approximate p-value using normal distribution for large df
        # For a proper implementation, scipy.stats.t.sf would be used.
        # This is a lightweight approximation.
        p_value = 2 * (1 - _normal_cdf(abs(t_stat)))

        return {
            "t_statistic": round(t_stat, 4),
            "p_value": round(p_value, 6),
            "significant": p_value < 0.05,
        }


def _normal_cdf(x: float) -> float:
    """Approximation of the standard normal CDF (Abramowitz and Stegun)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _evaluator_family(data: Dict[str, object]) -> str:
    """Classify persisted evaluator records without breaking older JSONL files."""
    recorded = data.get("evaluator_family")
    if recorded in {"deterministic", "semantic_judge"}:
        return str(recorded)
    strategy = str(data.get("evaluation_strategy", ""))
    if strategy in {"llm_judge", "multi_model_judge"}:
        return "semantic_judge"
    if strategy in {"exact_match", "regex"}:
        return "deterministic"
    return "other"


def calculate_metrics_from_jsonl(file_path: str | Path) -> BenchmarkMetrics:
    """Reads a results JSONL file and calculates aggregate metrics."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Results file not found: {path}")

    scores: List[float] = []
    latencies: List[float] = []
    generation_latencies: List[float] = []
    correct_count = 0
    total_count = 0
    total_prompt_tokens = 0
    total_completion_tokens = 0

    hit_1_sum = 0
    hit_3_sum = 0
    hit_5_sum = 0
    rr_sum = 0.0
    ndcg_sum = 0.0
    retrieval_evaluable_count = 0
    infrastructure_errors = 0
    answer_exact_matches: List[float] = []
    answer_token_f1_scores: List[float] = []
    deterministic_count = 0
    deterministic_correct = 0
    semantic_judge_count = 0
    semantic_judge_correct = 0
    semantic_judge_scores: List[float] = []

    failure_counts: Dict[str, int] = {}
    stage_latencies_sum: Dict[str, float] = {}
    stage_latencies_count: Dict[str, int] = {}

    engine = MetricsEngine()

    # Use a dictionary to deduplicate records in case of resumed benchmarks
    unique_records = {}

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue

            data = json.loads(line)
            # Create a unique key for each question evaluated in a specific iteration
            run_id = data.get("run_id", "unknown")
            iteration = data.get("iteration", 0)
            scenario_id = data.get("scenario_id", "unknown")
            question_id = data.get("question_id", "unknown")

            key = f"{run_id}_{iteration}_{scenario_id}_{question_id}"
            unique_records[key] = data

    for data in unique_records.values():
        total_count += 1

        score = float(data.get("score", 0.0))
        scores.append(score)

        if data.get("is_correct", False):
            correct_count += 1

        family = _evaluator_family(data)
        if family == "deterministic":
            deterministic_count += 1
            deterministic_correct += int(bool(data.get("is_correct", False)))
        elif family == "semantic_judge":
            semantic_judge_count += 1
            semantic_judge_correct += int(bool(data.get("is_correct", False)))
            semantic_judge_scores.append(score)

        raw_latency = data.get("retrieval_latency_ms", data.get("latency_ms"))
        if raw_latency is not None:
            latencies.append(float(raw_latency))
        generation_latency = data.get("generation_latency_ms")
        if generation_latency is not None:
            generation_latencies.append(float(generation_latency))
        if data.get("answer_exact_match") is not None:
            answer_exact_matches.append(float(data["answer_exact_match"]))
        if data.get("answer_token_f1") is not None:
            answer_token_f1_scores.append(float(data["answer_token_f1"]))

        if data.get("infrastructure_error", False):
            infrastructure_errors += 1

        total_prompt_tokens += int(data.get("prompt_tokens", 0))
        total_completion_tokens += int(data.get("completion_tokens", 0))

        # Retrieval metrics
        expected = data.get("expected_context_ids", [])
        retrieved = data.get("retrieved_context_ids", [])

        if expected:
            retrieval_evaluable_count += 1
            hit_1_sum += engine.calculate_hit_at_k(expected, retrieved, 1)
            hit_3_sum += engine.calculate_hit_at_k(expected, retrieved, 3)
            hit_5_sum += engine.calculate_hit_at_k(expected, retrieved, 5)
            rr_sum += engine.calculate_reciprocal_rank(expected, retrieved)

            ndcg_sum += engine.calculate_ndcg(expected, retrieved, k=5)

        # Diagnostics: failure attribution
        failure_attr = data.get("failure_attribution")
        if failure_attr and failure_attr != "SUCCESS":
            failure_counts[failure_attr] = failure_counts.get(failure_attr, 0) + 1

        # Diagnostics: stage latencies
        breakdown = data.get("latency_breakdown_ms", {})
        if isinstance(breakdown, dict):
            for stage, ms in breakdown.items():
                if isinstance(ms, (int, float)) and ms >= 0:
                    stage_latencies_sum[stage] = stage_latencies_sum.get(
                        stage, 0.0
                    ) + float(ms)
                    stage_latencies_count[stage] = (
                        stage_latencies_count.get(stage, 0) + 1
                    )

    if total_count == 0:
        return BenchmarkMetrics()

    accuracy = correct_count / total_count
    avg_score = sum(scores) / total_count
    avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

    # Percentile latencies
    p95_latency: Optional[float] = None
    p99_latency: Optional[float] = None
    if len(latencies) >= 20:
        sorted_lat = sorted(latencies)
        p95_latency = sorted_lat[math.ceil(len(sorted_lat) * 0.95) - 1]
        p99_latency = sorted_lat[math.ceil(len(sorted_lat) * 0.99) - 1]

    # Token efficiency
    token_efficiency = None
    total_tokens = total_prompt_tokens + total_completion_tokens
    if correct_count > 0 and total_tokens > 0:
        token_efficiency = total_tokens / correct_count

    avg_stage_latencies = {
        stage: round(stage_latencies_sum[stage] / stage_latencies_count[stage], 2)
        for stage in stage_latencies_sum
        if stage_latencies_count.get(stage, 0) > 0
    }

    return BenchmarkMetrics(
        total_questions=total_count,
        correct_answers=correct_count,
        accuracy=accuracy,
        avg_latency_ms=avg_latency,
        p95_latency_ms=p95_latency,
        p99_latency_ms=p99_latency,
        latency_sample_size=len(latencies),
        avg_score=avg_score,
        hit_at_1=(
            hit_1_sum / retrieval_evaluable_count if retrieval_evaluable_count else 0.0
        ),
        hit_at_3=(
            hit_3_sum / retrieval_evaluable_count if retrieval_evaluable_count else 0.0
        ),
        hit_at_5=(
            hit_5_sum / retrieval_evaluable_count if retrieval_evaluable_count else 0.0
        ),
        mrr=(rr_sum / retrieval_evaluable_count if retrieval_evaluable_count else 0.0),
        ndcg=(
            ndcg_sum / retrieval_evaluable_count if retrieval_evaluable_count else 0.0
        ),
        token_efficiency=token_efficiency,
        failure_attributions=failure_counts,
        avg_latency_breakdown_ms=avg_stage_latencies,
        retrieval_evaluable_questions=retrieval_evaluable_count,
        avg_generation_latency_ms=(
            sum(generation_latencies) / len(generation_latencies)
            if generation_latencies
            else None
        ),
        answer_exact_match=(
            sum(answer_exact_matches) / len(answer_exact_matches)
            if answer_exact_matches
            else None
        ),
        answer_token_f1=(
            sum(answer_token_f1_scores) / len(answer_token_f1_scores)
            if answer_token_f1_scores
            else None
        ),
        deterministic_evaluable_questions=deterministic_count,
        deterministic_correct_answers=deterministic_correct,
        deterministic_accuracy=(
            deterministic_correct / deterministic_count if deterministic_count else None
        ),
        semantic_judge_evaluable_questions=semantic_judge_count,
        semantic_judge_correct_answers=semantic_judge_correct,
        semantic_judge_accuracy=(
            semantic_judge_correct / semantic_judge_count
            if semantic_judge_count
            else None
        ),
        semantic_judge_avg_score=(
            sum(semantic_judge_scores) / len(semantic_judge_scores)
            if semantic_judge_scores
            else None
        ),
        infrastructure_errors=infrastructure_errors,
        valid=infrastructure_errors == 0,
    )
