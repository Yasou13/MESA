import pytest
from mesa_benchmark.metrics.calculator import (
    MetricsEngine,
)


class TestMetricsEngine:
    """Unit tests for the MetricsEngine class."""

    def test_hit_at_k_found_in_top_1(self) -> None:
        expected = ["ctx_1"]
        retrieved = ["ctx_1", "ctx_2", "ctx_3"]
        assert MetricsEngine.calculate_hit_at_k(expected, retrieved, 1) == 1

    def test_hit_at_k_found_in_top_3(self) -> None:
        expected = ["ctx_3"]
        retrieved = ["ctx_1", "ctx_2", "ctx_3"]
        assert MetricsEngine.calculate_hit_at_k(expected, retrieved, 3) == 1

    def test_hit_at_k_not_found(self) -> None:
        expected = ["ctx_5"]
        retrieved: list[str] = ["ctx_1", "ctx_2", "ctx_3"]
        assert MetricsEngine.calculate_hit_at_k(expected, retrieved, 3) == 0

    def test_hit_at_k_empty_retrieved(self) -> None:
        expected = ["ctx_1"]
        retrieved: list[str] = []
        assert MetricsEngine.calculate_hit_at_k(expected, retrieved, 5) == 0

    def test_reciprocal_rank_first(self) -> None:
        expected = ["ctx_1"]
        retrieved: list[str] = ["ctx_1", "ctx_2", "ctx_3"]
        assert MetricsEngine.calculate_reciprocal_rank(expected, retrieved) == 1.0

    def test_reciprocal_rank_second(self) -> None:
        expected = ["ctx_2"]
        retrieved = ["ctx_1", "ctx_2", "ctx_3"]
        assert MetricsEngine.calculate_reciprocal_rank(expected, retrieved) == 0.5

    def test_reciprocal_rank_not_found(self) -> None:
        expected = ["ctx_5"]
        retrieved = ["ctx_1", "ctx_2", "ctx_3"]
        assert MetricsEngine.calculate_reciprocal_rank(expected, retrieved) == 0.0

    def test_mrr_perfect_score(self) -> None:
        # All results at rank 1
        ranks = [1, 1, 1]
        assert MetricsEngine.calculate_mrr(ranks) == 1.0

    def test_mrr_mixed_scores(self) -> None:
        # Rank 1 (1/1), Rank 2 (1/2), Not found (0)
        # Average = (1.0 + 0.5 + 0.0) / 3 = 0.5
        ranks = [1, 2, 0]
        assert MetricsEngine.calculate_mrr(ranks) == pytest.approx(0.5)

    def test_mrr_empty_input(self) -> None:
        assert MetricsEngine.calculate_mrr([]) == 0.0

    def test_ndcg_perfect_ranking(self) -> None:
        # All relevant docs in perfect order
        expected = ["a", "b"]
        retrieved = ["a", "b", "c"]
        result = MetricsEngine.calculate_ndcg(expected, retrieved, k=3)
        assert result == 1.0

    def test_ndcg_empty(self) -> None:
        assert MetricsEngine.calculate_ndcg([], [], k=5) == 0.0

    def test_welch_t_test_identical_groups(self) -> None:
        a = [1.0, 1.0, 1.0, 1.0, 1.0]
        b = [1.0, 1.0, 1.0, 1.0, 1.0]
        result = MetricsEngine.welch_t_test(a, b)
        assert result["t_statistic"] == 0.0
        assert not result["significant"]

    def test_welch_t_test_different_groups(self) -> None:
        a = [10.0, 10.0, 10.0, 10.0, 10.0]
        b = [0.0, 0.0, 0.0, 0.0, 0.0]
        result = MetricsEngine.welch_t_test(a, b)
        assert result["significant"]

    def test_welch_t_test_insufficient_data(self) -> None:
        result = MetricsEngine.welch_t_test([1.0], [2.0])
        assert result["p_value"] == 1.0
