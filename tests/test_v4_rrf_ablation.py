from mesa_evals.v4_rrf_ablation import (
    evaluate_lane_ablation,
    fixed_legal_corpus,
    rrf_fuse,
)
from mesa_storage.retrieval_scope import V4_RRF_LANE_ORDER


def test_v4_rrf_lane_order_is_golden() -> None:
    assert V4_RRF_LANE_ORDER == ("vector", "bm25", "graph")


def test_fixed_legal_corpus_rrf_beats_vector_only() -> None:
    corpus, qrels = fixed_legal_corpus()

    report = evaluate_lane_ablation(corpus, qrels)

    assert report["scores"]["rrf_all"] > report["scores"]["vector_only"]
    assert report["delta_vs_vector"]["rrf_all"] > 0


def test_rrf_equal_scores_use_stable_artifact_id_tiebreak() -> None:
    assert rrf_fuse([["artifact-b", "artifact-a"], ["artifact-a", "artifact-b"]]) == [
        "artifact-a",
        "artifact-b",
    ]
