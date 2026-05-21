# MESA v0.3.0 — Phase 0 Async Evaluation Runner
# Instruments 5 execution abstraction paths against the Golden Dataset.
# Measures: Latency/TTFT (ms), Token Cost (input/output), Recall (%).
#
# Execution Paths:
#   Base            — Direct LLM (no retrieval augmentation)
#   Base_Vector     — LanceDB mock vector retrieval
#   Base_Graph      — SQLite mock graph retrieval
#   Base_Hybrid     — Vector + Graph fusion (RRF)
#   Base_Hybrid_FTS5 — Hybrid + SQLite FTS5 full-text search
"""
Asynchronous evaluation runner for the MESA v0.3.0 read path.

Each execution path simulates a distinct retrieval strategy and produces
structured metrics consumable by the gatekeeper CI/CD gate.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import random
import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from mesa_evals.dataset import DatasetEntry

logger = logging.getLogger("MESA_Evals")

# ---------------------------------------------------------------------------
# Metric structs — machine-parseable evaluation output
# ---------------------------------------------------------------------------

RESULTS_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "eval_results.json"


class ExecutionPath(str, Enum):
    """The 5 read-path abstraction levels under evaluation."""

    BASE = "Base"
    BASE_VECTOR = "Base_Vector"
    BASE_GRAPH = "Base_Graph"
    BASE_HYBRID = "Base_Hybrid"
    BASE_HYBRID_FTS5 = "Base_Hybrid_FTS5"


@dataclass
class TokenCost:
    """Input/output token accounting for a single query execution."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class EntryResult:
    """Per-entry evaluation result — one per (entry, path) pair."""

    entry_id: str
    path: str
    domain: str
    ttft_ms: float = 0.0
    token_cost: TokenCost = field(default_factory=TokenCost)
    recall: float = 0.0
    predicted_answer: str = ""
    ground_truth_answer: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "path": self.path,
            "domain": self.domain,
            "ttft_ms": round(self.ttft_ms, 3),
            "input_tokens": self.token_cost.input_tokens,
            "output_tokens": self.token_cost.output_tokens,
            "total_tokens": self.token_cost.total,
            "recall": round(self.recall, 4),
        }


@dataclass
class PathSummary:
    """Aggregated metrics for a single execution path across all entries."""

    path: str
    total_entries: int = 0
    mean_ttft_ms: float = 0.0
    p95_ttft_ms: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    mean_recall: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "total_entries": self.total_entries,
            "mean_ttft_ms": round(self.mean_ttft_ms, 3),
            "p95_ttft_ms": round(self.p95_ttft_ms, 3),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "mean_recall": round(self.mean_recall, 4),
        }


# ---------------------------------------------------------------------------
# Recall computation — token-level overlap between predicted and ground truth
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> set[str]:
    """Lowercase whitespace tokenization for recall computation."""
    return {w.strip(".,;:!?\"'()[]{}") for w in text.lower().split() if w.strip()}


def compute_recall(predicted: str, ground_truth: str) -> float:
    """Token-level recall: |predicted ∩ truth| / |truth|."""
    truth_tokens = _tokenize(ground_truth)
    if not truth_tokens:
        return 1.0 if not _tokenize(predicted) else 0.0
    pred_tokens = _tokenize(predicted)
    overlap = truth_tokens & pred_tokens
    return len(overlap) / len(truth_tokens)


# ---------------------------------------------------------------------------
# Mock token counter (mirrors DeterministicMockAdapter.get_token_count)
# ---------------------------------------------------------------------------


def _count_tokens(text: str) -> int:
    """Word-split token count consistent with the mock adapter."""
    return len(text.split())


# ---------------------------------------------------------------------------
# Deterministic mock embedding (mirrors DeterministicMockAdapter.embed)
# ---------------------------------------------------------------------------

_EMBEDDING_DIM = 384


def _mock_embed(text: str) -> list[float]:
    """SHA-256 seeded deterministic embedding."""
    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16) % (2 ** 32)
    rng = random.Random(seed)
    raw = [rng.gauss(0, 1) for _ in range(_EMBEDDING_DIM)]
    norm = math.sqrt(sum(x * x for x in raw)) or 1.0
    return [x / norm for x in raw]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Execution path simulators
# ---------------------------------------------------------------------------


async def _run_base(entry: DatasetEntry) -> EntryResult:
    """Base path: direct LLM call, no retrieval augmentation.

    Simulates sending the raw query to the LLM without any context injection.
    The 'predicted answer' is a deterministic mock response.
    """
    t0 = time.perf_counter_ns()

    prompt = f"Query: {entry.query}\nAnswer:"
    input_toks = _count_tokens(prompt)

    # Simulate LLM latency (deterministic based on query length)
    await asyncio.sleep(0.001 * (input_toks % 10 + 1))

    # Mock LLM output — no context so answer is generic
    predicted = (
        f"Based on the query, the answer relates to {entry.domain.value} domain."
    )
    output_toks = _count_tokens(predicted)

    ttft_ms = (time.perf_counter_ns() - t0) / 1_000_000

    return EntryResult(
        entry_id=entry.id,
        path=ExecutionPath.BASE.value,
        domain=entry.domain.value,
        ttft_ms=ttft_ms,
        token_cost=TokenCost(input_tokens=input_toks, output_tokens=output_toks),
        recall=compute_recall(predicted, entry.ground_truth_answer),
        predicted_answer=predicted,
        ground_truth_answer=entry.ground_truth_answer,
    )


async def _run_base_vector(entry: DatasetEntry) -> EntryResult:
    """Base_Vector path: LanceDB mock vector retrieval.

    Embeds the query + each context fragment, ranks by cosine similarity,
    injects the top-K fragments into the LLM prompt.
    """
    t0 = time.perf_counter_ns()

    query_emb = _mock_embed(entry.query)

    # Rank context fragments by cosine similarity to query
    scored = []
    for frag in entry.context_fragments:
        frag_emb = _mock_embed(frag)
        sim = _cosine_similarity(query_emb, frag_emb)
        scored.append((sim, frag))
    scored.sort(key=lambda x: x[0], reverse=True)

    # Take top-3 fragments
    retrieved = [frag for _, frag in scored[:3]]
    context_block = "\n".join(f"[{i+1}] {f}" for i, f in enumerate(retrieved))

    prompt = f"Context:\n{context_block}\n\nQuery: {entry.query}\nAnswer:"
    input_toks = _count_tokens(prompt)

    await asyncio.sleep(0.001 * (input_toks % 8 + 2))

    # Mock LLM generates answer seeded by retrieved context
    predicted = f"Based on the retrieved context: {retrieved[0][:120]}..."
    output_toks = _count_tokens(predicted)

    ttft_ms = (time.perf_counter_ns() - t0) / 1_000_000

    return EntryResult(
        entry_id=entry.id,
        path=ExecutionPath.BASE_VECTOR.value,
        domain=entry.domain.value,
        ttft_ms=ttft_ms,
        token_cost=TokenCost(input_tokens=input_toks, output_tokens=output_toks),
        recall=compute_recall(predicted, entry.ground_truth_answer),
        predicted_answer=predicted,
        ground_truth_answer=entry.ground_truth_answer,
    )


async def _run_base_graph(entry: DatasetEntry) -> EntryResult:
    """Base_Graph path: SQLite mock graph retrieval.

    Simulates a knowledge-graph lookup using an in-memory SQLite DB.
    Entities are extracted via simple tokenization and matched against
    stored context fragments.
    """
    t0 = time.perf_counter_ns()

    # Build ephemeral SQLite graph from context fragments
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE nodes (id INTEGER PRIMARY KEY, entity TEXT, fragment TEXT)"
    )
    conn.execute("CREATE TABLE edges (src INTEGER, dst INTEGER, relation TEXT)")

    entities_seen: dict[str, int] = {}
    node_id = 0
    for frag in entry.context_fragments:
        words = {w.strip(".,;:!?\"'()[]{}").lower() for w in frag.split() if len(w) > 3}
        for word in words:
            if word not in entities_seen:
                entities_seen[word] = node_id
                conn.execute(
                    "INSERT INTO nodes VALUES (?, ?, ?)", (node_id, word, frag)
                )
                node_id += 1

    # Link entities co-occurring in the same fragment
    frag_entities: list[list[int]] = []
    for frag in entry.context_fragments:
        ids_in_frag = []
        for w in frag.lower().split():
            w_clean = w.strip(".,;:!?\"'()[]{}").lower()
            if w_clean in entities_seen:
                ids_in_frag.append(entities_seen[w_clean])
        frag_entities.append(list(set(ids_in_frag)))

    for group in frag_entities:
        for i in range(len(group)):
            for j in range(i + 1, min(i + 3, len(group))):
                conn.execute(
                    "INSERT INTO edges VALUES (?, ?, ?)",
                    (group[i], group[j], "CO_OCCURS"),
                )
    conn.commit()

    # Query: extract entities from the query and traverse 1-hop
    query_words = {
        w.strip(".,;:!?\"'()[]{}").lower() for w in entry.query.split() if len(w) > 3
    }
    matched_ids = [entities_seen[w] for w in query_words if w in entities_seen]

    retrieved_fragments: list[str] = []
    if matched_ids:
        placeholders = ",".join("?" * len(matched_ids))
        # Direct matches
        rows = conn.execute(
            f"SELECT DISTINCT fragment FROM nodes WHERE id IN ({placeholders})",
            matched_ids,
        ).fetchall()
        retrieved_fragments.extend(r[0] for r in rows)

        # 1-hop neighbours
        neighbour_rows = conn.execute(
            f"SELECT DISTINCT n.fragment FROM edges e "
            f"JOIN nodes n ON n.id = e.dst "
            f"WHERE e.src IN ({placeholders})",
            matched_ids,
        ).fetchall()
        for r in neighbour_rows:
            if r[0] not in retrieved_fragments:
                retrieved_fragments.append(r[0])

    conn.close()

    if not retrieved_fragments:
        retrieved_fragments = entry.context_fragments[:1]

    context_block = "\n".join(
        f"[{i+1}] {f}" for i, f in enumerate(retrieved_fragments[:3])
    )
    prompt = f"Graph Context:\n{context_block}\n\nQuery: {entry.query}\nAnswer:"
    input_toks = _count_tokens(prompt)

    await asyncio.sleep(0.001 * (input_toks % 6 + 3))

    predicted = f"Graph traversal reveals: {retrieved_fragments[0][:120]}..."
    output_toks = _count_tokens(predicted)

    ttft_ms = (time.perf_counter_ns() - t0) / 1_000_000

    return EntryResult(
        entry_id=entry.id,
        path=ExecutionPath.BASE_GRAPH.value,
        domain=entry.domain.value,
        ttft_ms=ttft_ms,
        token_cost=TokenCost(input_tokens=input_toks, output_tokens=output_toks),
        recall=compute_recall(predicted, entry.ground_truth_answer),
        predicted_answer=predicted,
        ground_truth_answer=entry.ground_truth_answer,
    )


async def _run_base_hybrid(entry: DatasetEntry) -> EntryResult:
    """Base_Hybrid path: Vector + Graph fusion via Reciprocal Rank Fusion.

    Combines vector similarity rankings with graph traversal rankings
    using RRF (k=60), mirroring mesa_memory.retrieval.hybrid.HybridRetriever.
    """
    t0 = time.perf_counter_ns()

    # --- Vector ranking ---
    query_emb = _mock_embed(entry.query)
    vector_ranked: list[tuple[float, int, str]] = []
    for idx, frag in enumerate(entry.context_fragments):
        sim = _cosine_similarity(query_emb, _mock_embed(frag))
        vector_ranked.append((sim, idx, frag))
    vector_ranked.sort(key=lambda x: x[0], reverse=True)

    # --- Graph ranking (entity overlap count) ---
    query_words = _tokenize(entry.query)
    graph_ranked: list[tuple[int, int, str]] = []
    for idx, frag in enumerate(entry.context_fragments):
        frag_words = _tokenize(frag)
        overlap = len(query_words & frag_words)
        graph_ranked.append((overlap, idx, frag))
    graph_ranked.sort(key=lambda x: x[0], reverse=True)

    # --- RRF fusion (k=60, matching MESA config.rrf_k default) ---
    rrf_k = 60
    rrf_scores: dict[int, float] = {}
    for rank, (_, idx, _) in enumerate(vector_ranked):
        rrf_scores[idx] = rrf_scores.get(idx, 0.0) + 1.0 / (rrf_k + rank + 1)
    for rank, (_, idx, _) in enumerate(graph_ranked):
        rrf_scores[idx] = rrf_scores.get(idx, 0.0) + 1.0 / (rrf_k + rank + 1)

    fused_order = sorted(rrf_scores.keys(), key=lambda i: rrf_scores[i], reverse=True)
    retrieved = [entry.context_fragments[i] for i in fused_order[:3]]

    context_block = "\n".join(f"[{i+1}] {f}" for i, f in enumerate(retrieved))
    prompt = (
        f"Hybrid Context (Vector+Graph RRF):\n{context_block}\n\n"
        f"Query: {entry.query}\nAnswer:"
    )
    input_toks = _count_tokens(prompt)

    await asyncio.sleep(0.001 * (input_toks % 5 + 4))

    predicted = (
        f"Hybrid retrieval synthesises: {retrieved[0][:80]}... "
        f"Cross-referenced with: {retrieved[-1][:60]}..."
    )
    output_toks = _count_tokens(predicted)

    ttft_ms = (time.perf_counter_ns() - t0) / 1_000_000

    return EntryResult(
        entry_id=entry.id,
        path=ExecutionPath.BASE_HYBRID.value,
        domain=entry.domain.value,
        ttft_ms=ttft_ms,
        token_cost=TokenCost(input_tokens=input_toks, output_tokens=output_toks),
        recall=compute_recall(predicted, entry.ground_truth_answer),
        predicted_answer=predicted,
        ground_truth_answer=entry.ground_truth_answer,
    )


async def _run_base_hybrid_fts5(entry: DatasetEntry) -> EntryResult:
    """Base_Hybrid_FTS5 path: Hybrid + SQLite FTS5 full-text search.

    Extends Base_Hybrid by adding an FTS5 full-text index over context
    fragments.  FTS5 match scores are fused into the RRF as a third signal.
    """
    t0 = time.perf_counter_ns()

    # --- FTS5 index ---
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE fts_frags USING fts5(idx, content)")
    for idx, frag in enumerate(entry.context_fragments):
        conn.execute(
            "INSERT INTO fts_frags (idx, content) VALUES (?, ?)",
            (str(idx), frag),
        )
    conn.commit()

    # Extract key terms for FTS query (simple: longest 3 words)
    query_words = sorted(
        (w for w in entry.query.split() if len(w) > 3),
        key=len,
        reverse=True,
    )[:3]
    fts_query = " OR ".join(f'"{w}"' for w in query_words) if query_words else "*"

    fts_ranked: list[tuple[float, int]] = []
    try:
        rows = conn.execute(
            "SELECT idx, rank FROM fts_frags WHERE fts_frags MATCH ? " "ORDER BY rank",
            (fts_query,),
        ).fetchall()
        for row in rows:
            fts_ranked.append((abs(float(row[1])), int(row[0])))
    except sqlite3.OperationalError:
        # FTS query syntax error — degrade gracefully
        fts_ranked = [(0.0, i) for i in range(len(entry.context_fragments))]
    conn.close()

    # --- Vector ranking ---
    query_emb = _mock_embed(entry.query)
    vector_ranked: list[tuple[float, int]] = []
    for idx, frag in enumerate(entry.context_fragments):
        sim = _cosine_similarity(query_emb, _mock_embed(frag))
        vector_ranked.append((sim, idx))
    vector_ranked.sort(key=lambda x: x[0], reverse=True)

    # --- Graph ranking ---
    q_tokens = _tokenize(entry.query)
    graph_ranked: list[tuple[int, int]] = []
    for idx, frag in enumerate(entry.context_fragments):
        overlap = len(q_tokens & _tokenize(frag))
        graph_ranked.append((overlap, idx))
    graph_ranked.sort(key=lambda x: x[0], reverse=True)

    # --- 3-way RRF (k=60) ---
    rrf_k = 60
    rrf_scores: dict[int, float] = {}
    for rank, (_, idx) in enumerate(vector_ranked):
        rrf_scores[idx] = rrf_scores.get(idx, 0.0) + 1.0 / (rrf_k + rank + 1)
    for rank, (_, idx) in enumerate(graph_ranked):
        rrf_scores[idx] = rrf_scores.get(idx, 0.0) + 1.0 / (rrf_k + rank + 1)
    for rank, (_, idx) in enumerate(fts_ranked):
        rrf_scores[idx] = rrf_scores.get(idx, 0.0) + 1.0 / (rrf_k + rank + 1)

    fused_order = sorted(rrf_scores.keys(), key=lambda i: rrf_scores[i], reverse=True)
    retrieved = [entry.context_fragments[i] for i in fused_order[:3]]

    context_block = "\n".join(f"[{i+1}] {f}" for i, f in enumerate(retrieved))
    prompt = (
        f"Hybrid+FTS5 Context (Vector+Graph+FTS5 RRF):\n{context_block}\n\n"
        f"Query: {entry.query}\nAnswer:"
    )
    input_toks = _count_tokens(prompt)

    await asyncio.sleep(0.001 * (input_toks % 4 + 5))

    predicted = (
        f"FTS5-augmented retrieval: {retrieved[0][:80]}... "
        f"Full-text match confirms: {retrieved[-1][:60]}..."
    )
    output_toks = _count_tokens(predicted)

    ttft_ms = (time.perf_counter_ns() - t0) / 1_000_000

    return EntryResult(
        entry_id=entry.id,
        path=ExecutionPath.BASE_HYBRID_FTS5.value,
        domain=entry.domain.value,
        ttft_ms=ttft_ms,
        token_cost=TokenCost(input_tokens=input_toks, output_tokens=output_toks),
        recall=compute_recall(predicted, entry.ground_truth_answer),
        predicted_answer=predicted,
        ground_truth_answer=entry.ground_truth_answer,
    )


# ---------------------------------------------------------------------------
# Path dispatcher
# ---------------------------------------------------------------------------

_PATH_RUNNERS = {
    ExecutionPath.BASE: _run_base,
    ExecutionPath.BASE_VECTOR: _run_base_vector,
    ExecutionPath.BASE_GRAPH: _run_base_graph,
    ExecutionPath.BASE_HYBRID: _run_base_hybrid,
    ExecutionPath.BASE_HYBRID_FTS5: _run_base_hybrid_fts5,
}


# ---------------------------------------------------------------------------
# Aggregation utilities
# ---------------------------------------------------------------------------


def _aggregate_path(path: str, results: list[EntryResult]) -> PathSummary:
    """Compute aggregate metrics for a single execution path."""
    if not results:
        return PathSummary(path=path)

    ttfts = [r.ttft_ms for r in results]
    ttfts_sorted = sorted(ttfts)
    p95_idx = int(math.ceil(0.95 * len(ttfts_sorted))) - 1

    return PathSummary(
        path=path,
        total_entries=len(results),
        mean_ttft_ms=sum(ttfts) / len(ttfts),
        p95_ttft_ms=ttfts_sorted[max(0, p95_idx)],
        total_input_tokens=sum(r.token_cost.input_tokens for r in results),
        total_output_tokens=sum(r.token_cost.output_tokens for r in results),
        mean_recall=sum(r.recall for r in results) / len(results),
    )


# ---------------------------------------------------------------------------
# Main evaluation orchestrator
# ---------------------------------------------------------------------------


async def run_evaluation(
    entries: list[dict[str, Any]],
    paths: list[ExecutionPath] | None = None,
) -> dict[str, Any]:
    """Execute all evaluation paths against the provided dataset entries.

    Args:
        entries: List of dict-serialised DatasetEntry objects.
        paths: Subset of ExecutionPath to run. Defaults to all 5.

    Returns:
        A structured dict with per-entry results and per-path summaries,
        serialisable to JSON for gatekeeper consumption.
    """
    if paths is None:
        paths = list(ExecutionPath)

    parsed_entries = [DatasetEntry.model_validate(e) for e in entries]

    all_results: list[EntryResult] = []
    path_results: dict[str, list[EntryResult]] = {p.value: [] for p in paths}

    for path_enum in paths:
        runner = _PATH_RUNNERS[path_enum]
        logger.info(
            "EVAL_PATH_START path=%s entries=%d", path_enum.value, len(parsed_entries)
        )

        tasks = [runner(entry) for entry in parsed_entries]
        results = await asyncio.gather(*tasks)

        for r in results:
            all_results.append(r)
            path_results[path_enum.value].append(r)

        logger.info("EVAL_PATH_DONE path=%s", path_enum.value)

    # Aggregate summaries
    summaries = {
        path_name: _aggregate_path(path_name, res).to_dict()
        for path_name, res in path_results.items()
    }

    output = {
        "mesa_version": "0.3.0",
        "evaluation_paths": [p.value for p in paths],
        "total_entries": len(parsed_entries),
        "summaries": summaries,
        "results": [r.to_dict() for r in all_results],
    }

    return output


def write_results(results: dict[str, Any], path: Path | None = None) -> Path:
    """Persist evaluation results to JSON for gatekeeper consumption."""
    out = path or RESULTS_OUTPUT_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    logger.info("EVAL_RESULTS_WRITTEN path=%s", out)
    return out


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


async def _main() -> None:
    """Run the full evaluation suite and write results."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        stream=sys.stderr,
    )

    from mesa_evals.generator import generate_synthetic_entries

    entries = generate_synthetic_entries()
    print(f"MESA_EVAL | Loaded {len(entries)} synthetic entries", file=sys.stderr)

    results = await run_evaluation(entries)
    out_path = write_results(results)

    # Print structured summary to stdout for CI log parsing
    print("MESA_EVAL | === PATH SUMMARIES ===")
    for path_name, summary in results["summaries"].items():
        print(
            f"MESA_EVAL | path={path_name:<20s} "
            f"mean_ttft_ms={summary['mean_ttft_ms']:>10.3f} "
            f"p95_ttft_ms={summary['p95_ttft_ms']:>10.3f} "
            f"input_tokens={summary['total_input_tokens']:>8d} "
            f"output_tokens={summary['total_output_tokens']:>8d} "
            f"mean_recall={summary['mean_recall']:>8.4f}"
        )
    print(f"MESA_EVAL | Results written to {out_path}")


if __name__ == "__main__":
    asyncio.run(_main())
