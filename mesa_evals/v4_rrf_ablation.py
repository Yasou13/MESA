"""Deterministic V4 RRF lane-ablation evaluator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def rrf_fuse(rankings: list[list[str]], *, k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, artifact_id in enumerate(ranking, start=1):
            scores[artifact_id] = scores.get(artifact_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda item: (-scores[item], item))


def _mean_reciprocal_rank(
    runs: dict[str, list[str]], qrels: dict[str, set[str]]
) -> float:
    total = 0.0
    for query_id, relevant in qrels.items():
        ranking = runs.get(query_id, [])
        rank = next(
            (position for position, item in enumerate(ranking, 1) if item in relevant),
            None,
        )
        total += 1.0 / rank if rank else 0.0
    return total / len(qrels) if qrels else 0.0


def evaluate_lane_ablation(
    corpus: dict[str, dict[str, list[str]]],
    qrels: dict[str, set[str]],
) -> dict[str, Any]:
    """Compare vector-only with every deterministic RRF lane combination."""
    lane_sets = {
        "vector_only": ("vector",),
        "vector_bm25": ("vector", "bm25"),
        "vector_graph": ("vector", "graph"),
        "rrf_all": ("vector", "bm25", "graph"),
    }
    metrics: dict[str, float] = {}
    runs: dict[str, dict[str, list[str]]] = {}
    for name, lanes in lane_sets.items():
        run = {
            query_id: rrf_fuse([lane_results.get(lane, []) for lane in lanes])
            for query_id, lane_results in corpus.items()
        }
        runs[name] = run
        metrics[name] = _mean_reciprocal_rank(run, qrels)
    return {
        "metric": "MRR",
        "scores": metrics,
        "delta_vs_vector": {
            name: score - metrics["vector_only"]
            for name, score in metrics.items()
            if name != "vector_only"
        },
        "runs": runs,
    }


def fixed_legal_corpus() -> tuple[dict[str, dict[str, list[str]]], dict[str, set[str]]]:
    """Return the small, offline regression corpus used by CI."""
    return (
        {
            "q-kvkk": {
                "vector": ["commentary", "kvkk-6698"],
                "bm25": ["kvkk-6698", "commentary"],
                "graph": ["kvkk-6698"],
            },
            "q-aym": {
                "vector": ["blog", "aym-decision"],
                "bm25": ["aym-decision", "blog"],
                "graph": ["aym-decision"],
            },
        },
        {
            "q-kvkk": {"kvkk-6698"},
            "q-aym": {"aym-decision"},
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write the deterministic MESA V4 RRF lane-ablation report."
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    corpus, qrels = fixed_legal_corpus()
    report = evaluate_lane_ablation(corpus, qrels)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
