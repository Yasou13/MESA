# MESA v0.5.1 — Antigravity Contradiction Benchmark Runner
# Async executor with semaphore-throttled concurrency, zero-cost
# telemetry (time.monotonic), P99 latency, and multi-client tier support.
"""
Contradiction resolution & recency bias benchmark runner.

Features:
  - ``asyncio.Semaphore(3)`` throttled concurrency for LLM rate-limit safety
  - Per-query ``time.monotonic()`` precision timing
  - P99 latency and CRA (Context Resolution Accuracy) per client tier
  - Structured telemetry log: scenario_id, client, response, match, latency
  - Strict namespace isolation via deterministic agent_id per scenario

Usage::

    python -m mesa_evals.contradiction_runner --client barerag --output results.json
    python -m mesa_evals.contradiction_runner --client barerag --concurrency 5 --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Add workspace root to python path to allow imports from mesa_evals
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from mesa_evals.clients.base import BaseMemoryClient, QueryResult

logger = logging.getLogger("MESA_ContradictionRunner")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_DATASET_PATH = "benchmarks/phase1_gatekeeper/data/contradiction_benchmark.json"
DEFAULT_SEARCH_LIMIT = 5
DEFAULT_CONCURRENCY = 3  # Semaphore limit — prevents 429 rate-limit errors

AGENT_ID_PREFIX = "benchmark_CONF"


# ---------------------------------------------------------------------------
# Telemetry — zero-cost latency tracking
# ---------------------------------------------------------------------------


@dataclass
class LatencyTracker:
    """Collects per-query latencies for percentile computation."""

    _samples: list[float] = field(default_factory=list)

    def record(self, latency_ms: float) -> None:
        self._samples.append(latency_ms)

    @property
    def count(self) -> int:
        return len(self._samples)

    @property
    def mean_ms(self) -> float:
        return sum(self._samples) / len(self._samples) if self._samples else 0.0

    @property
    def p50_ms(self) -> float:
        return self._percentile(50)

    @property
    def p95_ms(self) -> float:
        return self._percentile(95)

    @property
    def p99_ms(self) -> float:
        return self._percentile(99)

    @property
    def min_ms(self) -> float:
        return min(self._samples) if self._samples else 0.0

    @property
    def max_ms(self) -> float:
        return max(self._samples) if self._samples else 0.0

    def _percentile(self, p: float) -> float:
        if not self._samples:
            return 0.0
        sorted_s = sorted(self._samples)
        k = (len(sorted_s) - 1) * (p / 100.0)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_s[int(k)]
        return sorted_s[f] * (c - k) + sorted_s[c] * (k - f)

    def snapshot(self) -> dict[str, float]:
        return {
            "count": self.count,
            "mean_ms": round(self.mean_ms, 2),
            "p50_ms": round(self.p50_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
            "p99_ms": round(self.p99_ms, 2),
            "min_ms": round(self.min_ms, 2),
            "max_ms": round(self.max_ms, 2),
        }


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ScenarioResult:
    """Per-scenario evaluation result with full telemetry."""

    scenario_id: str
    client_type: str
    category: str
    expected_resolution: str
    predicted_resolution: str
    correct: bool
    keyword_hits: list[str]
    keyword_misses: list[str]
    keyword_match: bool  # any_of match success
    match_score: float
    query: str
    raw_response: str
    latency_s: float  # seconds (high-precision)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def log_line(self) -> str:
        """Structured telemetry log line."""
        status = "✅" if self.correct else "❌"
        match = "HIT" if self.keyword_match else "MISS"
        return (
            f"{status} | {self.scenario_id:>8s} | {self.client_type:<8s} | "
            f"match={match:<4s} | expected={self.expected_resolution} | "
            f"predicted={self.predicted_resolution} | "
            f"latency={self.latency_s:.3f}s | "
            f"keywords={len(self.keyword_hits)}/{len(self.keyword_hits) + len(self.keyword_misses)}"
        )


@dataclass
class BenchmarkReport:
    """Aggregate report with CRA and P99 metrics."""

    client_name: str
    client_type: str
    dataset_path: str
    total_scenarios: int = 0
    correct: int = 0
    incorrect: int = 0
    errors: int = 0
    accuracy: float = 0.0  # CRA = Context Resolution Accuracy

    # Recency bias split
    t0_valid_total: int = 0
    t0_valid_correct: int = 0
    t0_valid_accuracy: float = 0.0
    t1_valid_total: int = 0
    t1_valid_correct: int = 0
    t1_valid_accuracy: float = 0.0

    mean_keyword_score: float = 0.0
    elapsed_total_s: float = 0.0
    latency: dict[str, float] = field(default_factory=dict)

    scenario_results: list[ScenarioResult] = field(default_factory=list)

    def compute_aggregates(self, tracker: LatencyTracker) -> None:
        n = len(self.scenario_results)
        if n == 0:
            return

        self.total_scenarios = n
        self.correct = sum(1 for r in self.scenario_results if r.correct)
        self.incorrect = sum(
            1 for r in self.scenario_results if not r.correct and r.error is None
        )
        self.errors = sum(1 for r in self.scenario_results if r.error is not None)
        self.accuracy = self.correct / n

        t0 = [r for r in self.scenario_results if r.expected_resolution == "t0_valid"]
        t1 = [r for r in self.scenario_results if r.expected_resolution == "t1_valid"]

        self.t0_valid_total = len(t0)
        self.t0_valid_correct = sum(1 for r in t0 if r.correct)
        self.t0_valid_accuracy = (
            self.t0_valid_correct / self.t0_valid_total if t0 else 0.0
        )

        self.t1_valid_total = len(t1)
        self.t1_valid_correct = sum(1 for r in t1 if r.correct)
        self.t1_valid_accuracy = (
            self.t1_valid_correct / self.t1_valid_total if t1 else 0.0
        )

        scores = [r.match_score for r in self.scenario_results]
        self.mean_keyword_score = sum(scores) / len(scores) if scores else 0.0
        self.latency = tracker.snapshot()

    def to_json(self) -> dict[str, Any]:
        return {
            "benchmark": "Antigravity_Contradiction_Resolution",
            "version": "0.5.1",
            "client": self.client_name,
            "client_type": self.client_type,
            "dataset": self.dataset_path,
            "summary": {
                "total_scenarios": self.total_scenarios,
                "correct": self.correct,
                "incorrect": self.incorrect,
                "errors": self.errors,
                "CRA": round(self.accuracy, 4),
                "mean_keyword_score": round(self.mean_keyword_score, 4),
                "elapsed_total_s": round(self.elapsed_total_s, 2),
            },
            "latency": self.latency,
            "recency_bias_analysis": {
                "t0_valid": {
                    "total": self.t0_valid_total,
                    "correct": self.t0_valid_correct,
                    "accuracy": round(self.t0_valid_accuracy, 4),
                },
                "t1_valid": {
                    "total": self.t1_valid_total,
                    "correct": self.t1_valid_correct,
                    "accuracy": round(self.t1_valid_accuracy, 4),
                },
            },
            "per_scenario": [r.to_dict() for r in self.scenario_results],
        }


# ---------------------------------------------------------------------------
# Dataset loader
# ---------------------------------------------------------------------------


def load_contradiction_dataset(path: str) -> list[dict[str, Any]]:
    """Load and validate the contradiction benchmark dataset."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Benchmark dataset not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list) or len(data) == 0:
        raise ValueError("Dataset must be a non-empty JSON array.")

    required_keys = {
        "scenario_id",
        "category",
        "t0_context",
        "t1_context",
        "query",
        "ground_truth_keywords",
        "expected_resolution",
    }
    for i, scenario in enumerate(data):
        missing = required_keys - set(scenario.keys())
        if missing:
            raise ValueError(f"Scenario {i} missing required keys: {missing}")
        if scenario["expected_resolution"] not in ("t0_valid", "t1_valid"):
            raise ValueError(
                f"Scenario {i} invalid expected_resolution: "
                f"{scenario['expected_resolution']!r}"
            )

    logger.info("DATASET_LOADED | path=%s scenarios=%d", path, len(data))
    return data


# ---------------------------------------------------------------------------
# Agent ID factory
# ---------------------------------------------------------------------------


def make_agent_id(scenario_id: str) -> str:
    """Deterministic, isolated agent_id: ``benchmark_CONF_001``."""
    return f"{AGENT_ID_PREFIX}_{scenario_id.replace('CONF_', '')}"


# ---------------------------------------------------------------------------
# Keyword evaluator
# ---------------------------------------------------------------------------


def evaluate_keywords(
    retrieved_context: str,
    ground_truth_keywords: list[str],
    match_mode: str = "any_of",
) -> tuple[list[str], list[str], float, bool]:
    """Evaluate keyword presence.  Returns (hits, misses, score, any_match)."""
    if not ground_truth_keywords:
        return [], [], 0.0, False

    context_lower = retrieved_context.lower()
    hits = [kw for kw in ground_truth_keywords if kw.lower() in context_lower]
    misses = [kw for kw in ground_truth_keywords if kw.lower() not in context_lower]

    if match_mode == "all_of":
        score = 1.0 if not misses else 0.0
    else:
        score = len(hits) / len(ground_truth_keywords)

    return hits, misses, score, len(hits) > 0


def predict_resolution(match_score: float, expected: str) -> str:
    """Heuristic: keywords found → correct side; absent → wrong side."""
    if match_score > 0.0:
        return expected
    return "t1_valid" if expected == "t0_valid" else "t0_valid"


# ---------------------------------------------------------------------------
# Per-scenario async executor (semaphore-throttled)
# ---------------------------------------------------------------------------


async def execute_scenario(
    client: BaseMemoryClient,
    scenario: dict[str, Any],
    *,
    client_type: str,
    semaphore: asyncio.Semaphore,
    tracker: LatencyTracker,
    search_limit: int = DEFAULT_SEARCH_LIMIT,
) -> ScenarioResult:
    """Execute one scenario under semaphore throttle with precision timing."""
    scenario_id = scenario["scenario_id"]
    agent_id = make_agent_id(scenario_id)

    async with semaphore:
        logger.info(
            "SCENARIO_START | id=%s agent_id=%s category=%s",
            scenario_id,
            agent_id,
            scenario["category"],
        )

        t_total_start = time.monotonic()

        try:
            # PHASE 1: Pristine isolation
            await client.clear_memory(agent_id=agent_id)

            # PHASE 2: Ingest t0
            await client.add_memory(
                scenario["t0_context"],
                agent_id=agent_id,
                metadata={"temporal_layer": "t0", "scenario_id": scenario_id},
            )

            # PHASE 3: Ingest t1
            await client.add_memory(
                scenario["t1_context"],
                agent_id=agent_id,
                metadata={"temporal_layer": "t1", "scenario_id": scenario_id},
            )

            # PHASE 4: Query — precision timing
            t_query_start = time.monotonic()
            result: QueryResult = await client.query(
                scenario["query"],
                agent_id=agent_id,
                limit=search_limit,
            )
            query_latency_s = time.monotonic() - t_query_start
            tracker.record(query_latency_s * 1000.0)

            total_latency_s = time.monotonic() - t_total_start

            # PHASE 5: Evaluate
            hits, misses, score, any_match = evaluate_keywords(
                result.context,
                scenario["ground_truth_keywords"],
                scenario.get("match_mode", "any_of"),
            )

            predicted = predict_resolution(score, scenario["expected_resolution"])
            correct = predicted == scenario["expected_resolution"]

            sr = ScenarioResult(
                scenario_id=scenario_id,
                client_type=client_type,
                category=scenario["category"],
                expected_resolution=scenario["expected_resolution"],
                predicted_resolution=predicted,
                correct=correct,
                keyword_hits=hits,
                keyword_misses=misses,
                keyword_match=any_match,
                match_score=score,
                query=scenario["query"],
                raw_response=result.context[:500],
                latency_s=round(query_latency_s, 6),
                error=result.error,
            )

        except Exception as exc:
            total_latency_s = time.monotonic() - t_total_start
            logger.error("SCENARIO_ERROR | id=%s error=%s", scenario_id, exc)
            sr = ScenarioResult(
                scenario_id=scenario_id,
                client_type=client_type,
                category=scenario["category"],
                expected_resolution=scenario["expected_resolution"],
                predicted_resolution="error",
                correct=False,
                keyword_hits=[],
                keyword_misses=scenario["ground_truth_keywords"],
                keyword_match=False,
                match_score=0.0,
                query=scenario["query"],
                raw_response="",
                latency_s=round(total_latency_s, 6),
                error=str(exc),
            )

        finally:
            try:
                await client.clear_memory(agent_id=agent_id)
            except Exception as cleanup_exc:
                logger.warning(
                    "SCENARIO_CLEANUP_ERROR | id=%s error=%s",
                    scenario_id,
                    cleanup_exc,
                )

        # Structured telemetry log
        logger.info("TELEMETRY | %s", sr.log_line())
        return sr


# ---------------------------------------------------------------------------
# Async benchmark orchestrator
# ---------------------------------------------------------------------------


async def run_benchmark(
    client: BaseMemoryClient,
    *,
    client_type: str = "barerag",
    dataset_path: str = DEFAULT_DATASET_PATH,
    search_limit: int = DEFAULT_SEARCH_LIMIT,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> BenchmarkReport:
    """Execute the full benchmark with semaphore-throttled async concurrency.

    Uses ``asyncio.Semaphore(concurrency)`` to cap parallel API calls,
    preventing 429 rate-limit errors from LLM providers.
    """
    scenarios = load_contradiction_dataset(dataset_path)

    tracker = LatencyTracker()
    semaphore = asyncio.Semaphore(concurrency)

    report = BenchmarkReport(
        client_name=repr(client),
        client_type=client_type,
        dataset_path=dataset_path,
    )

    t_total = time.monotonic()
    await client.initialize()

    try:
        tasks = [
            execute_scenario(
                client,
                scenario,
                client_type=client_type,
                semaphore=semaphore,
                tracker=tracker,
                search_limit=search_limit,
            )
            for scenario in scenarios
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, Exception):
                logger.error("GATHER_EXCEPTION | %s", r)
            elif isinstance(r, ScenarioResult):
                report.scenario_results.append(r)

    finally:
        await client.shutdown()

    report.elapsed_total_s = time.monotonic() - t_total
    report.compute_aggregates(tracker)

    # Final summary log
    logger.info("═══════════════════════════════════════════════════════════════")
    logger.info(
        "BENCHMARK_COMPLETE | client=%s CRA=%.1f%% "
        "t0_acc=%.1f%% t1_acc=%.1f%% "
        "P99=%.1fms elapsed=%.1fs",
        client_type,
        report.accuracy * 100,
        report.t0_valid_accuracy * 100,
        report.t1_valid_accuracy * 100,
        tracker.p99_ms,
        report.elapsed_total_s,
    )
    logger.info("═══════════════════════════════════════════════════════════════")

    return report


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------


def create_client(client_type: str) -> BaseMemoryClient:
    """Instantiate a benchmark client by type name."""
    if client_type == "barerag":
        from mesa_evals.clients.barerag import BareRAGClient
        from mesa_memory.adapter.mock import DeterministicMockAdapter

        return BareRAGClient(
            adapter=DeterministicMockAdapter(),
            storage_root="./storage/benchmark_barerag",
        )
    elif client_type == "mesa":
        from mesa_evals.clients.mesa import MesaClient
        from mesa_memory.adapter.mock import DeterministicMockAdapter

        return MesaClient(adapter=DeterministicMockAdapter())
    elif client_type == "mem0":
        from mesa_evals.clients.mem0 import Mem0Client
        from mesa_memory.adapter.mock import DeterministicMockAdapter

        return Mem0Client(adapter=DeterministicMockAdapter())
    raise ValueError(f"Unknown client type: {client_type!r}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MESA v0.5.1 — Antigravity Contradiction Benchmark",
    )
    parser.add_argument("--dataset", default=DEFAULT_DATASET_PATH)
    parser.add_argument(
        "--client", default="barerag", choices=["barerag", "mesa", "mem0"]
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_SEARCH_LIMIT)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--output", default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    client = create_client(args.client)
    report = asyncio.run(
        run_benchmark(
            client,
            client_type=args.client,
            dataset_path=args.dataset,
            search_limit=args.limit,
            concurrency=args.concurrency,
        )
    )

    report_json = report.to_json()

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report_json, f, indent=2, ensure_ascii=False)
        logger.info("REPORT_WRITTEN | path=%s", args.output)
    else:
        print(json.dumps(report_json, indent=2, ensure_ascii=False))

    sys.exit(0 if report.accuracy >= 0.5 else 1)


if __name__ == "__main__":
    main()
