from mesa_evals.v4_rrf_ablation import evaluate_lane_ablation, fixed_legal_corpus


def test_fixed_legal_corpus_rrf_beats_vector_only() -> None:
    corpus, qrels = fixed_legal_corpus()

    report = evaluate_lane_ablation(corpus, qrels)

    assert report["scores"]["rrf_all"] > report["scores"]["vector_only"]
    assert report["delta_vs_vector"]["rrf_all"] > 0
