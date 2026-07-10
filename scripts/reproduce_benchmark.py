# ruff: noqa: E402
#!/usr/bin/env python3
"""
MESA Reproducibility Benchmark Runner.

Executes multi-seed runs of the MESA memory engine benchmark, recording
real statistical variance (Mean ± Std), computing Welch's t-test p-values
against a baseline, and generating a verified reproducibility report.

NOTE: This script runs REAL benchmarks — no dry-run mode with fake data.
      If you need a quick test, use --max-scenarios to limit the dataset.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "mesa-benchmark"))

from mesa_benchmark.core.runner import BenchmarkRunner
from mesa_benchmark.reports.statistics import (
    compute_run_statistics,
    compute_t_test_p_value,
)


def collect_metrics_from_jsonl(results_file: str) -> Dict[str, Any]:
    """Reads a results JSONL file and extracts accuracy + latency."""
    path = Path(results_file)
    if not path.exists():
        return {"accuracy": 0.0, "avg_latency_ms": 0.0}

    total = 0
    correct = 0
    latencies: List[float] = []

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            total += 1
            if data.get("is_correct", False):
                correct += 1
            lat = data.get("latency_ms")
            if lat is not None:
                latencies.append(float(lat))

    accuracy = (correct / total * 100.0) if total > 0 else 0.0
    avg_lat = sum(latencies) / len(latencies) if latencies else 0.0

    return {
        "accuracy": accuracy,
        "avg_latency_ms": avg_lat,
        "total": total,
        "correct": correct,
    }


def run_single_seed(
    config_path: str,
    seed: int,
    max_scenarios: int | None = None,
) -> Dict[str, Any]:
    """
    Runs a single benchmark iteration with the given seed.
    Returns metrics parsed from the generated JSONL.
    """
    runner = BenchmarkRunner(config_path=config_path)
    runner.run_id = f"reproduce_seed_{seed}"

    # Override seed in config
    runner.setup()
    assert runner.config is not None
    runner.config.seed = seed
    runner.config.iterations = 1  # Single iteration per seed

    # Optionally limit dataset size for quick testing
    if max_scenarios and runner.dataset_manager:
        runner.dataset_manager.scenarios = runner.dataset_manager.scenarios[
            :max_scenarios
        ]

    runner.run()

    # Read metrics from generated JSONL
    results_file = f"results_{runner.run_id}.jsonl"
    metrics = collect_metrics_from_jsonl(results_file)
    metrics["seed"] = seed
    metrics["results_file"] = results_file

    return metrics


def main():
    parser = argparse.ArgumentParser(
        description="Run reproducible multi-seed MESA benchmarks with real data."
    )
    parser.add_argument(
        "--config",
        type=str,
        default=str(REPO_ROOT / "mesa-benchmark" / "config.yaml"),
        help="Path to benchmark config.yaml",
    )
    parser.add_argument(
        "--seeds",
        type=str,
        default="42,43,44,45,46",
        help="Comma-separated list of random seeds (default: 5 seeds for statistical rigor)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="reproducibility_report.json",
        help="Output report JSON path",
    )
    parser.add_argument(
        "--max-scenarios",
        type=int,
        default=None,
        help="Limit number of scenarios per run (for quick testing)",
    )
    parser.add_argument(
        "--baseline-config",
        type=str,
        default=None,
        help="Optional: config for baseline system to compute p-value against",
    )

    args = parser.parse_args()

    seed_list = [int(s.strip()) for s in args.seeds.split(",") if s.strip()]
    if not seed_list:
        seed_list = [42]

    print("=== MESA Reproducibility Runner ===")
    print(f"Config: {args.config}")
    print(f"Seeds: {seed_list}")
    print(f"Max scenarios: {args.max_scenarios or 'ALL'}")
    print()

    # --- Run MESA across all seeds ---
    mesa_accuracies: List[float] = []
    mesa_latencies: List[float] = []
    all_seed_metrics: List[Dict[str, Any]] = []

    for seed in seed_list:
        print(f"\n{'='*50}")
        print(f"--- Running MESA with Seed {seed} ---")
        print(f"{'='*50}")

        try:
            metrics = run_single_seed(args.config, seed, args.max_scenarios)
            mesa_accuracies.append(metrics["accuracy"])
            mesa_latencies.append(metrics["avg_latency_ms"])
            all_seed_metrics.append(metrics)
            print(
                f"  Seed {seed} → Accuracy: {metrics['accuracy']:.2f}%, "
                f"Latency: {metrics['avg_latency_ms']:.2f}ms"
            )
        except Exception as exc:
            print(f"  ERROR on seed {seed}: {exc}")
            # Record failure but continue with other seeds
            all_seed_metrics.append(
                {
                    "seed": seed,
                    "accuracy": 0.0,
                    "avg_latency_ms": 0.0,
                    "error": str(exc),
                }
            )

    # --- Compute statistics ---
    acc_stats = compute_run_statistics(mesa_accuracies)
    lat_stats = compute_run_statistics(mesa_latencies)

    # --- Optional: run baseline and compute p-value ---
    baseline_stats = None
    significance_test = None
    if args.baseline_config:
        print(f"\n{'='*50}")
        print("--- Running BASELINE for comparison ---")
        print(f"{'='*50}")

        baseline_accuracies: List[float] = []
        for seed in seed_list:
            try:
                b_metrics = run_single_seed(
                    args.baseline_config, seed, args.max_scenarios
                )
                baseline_accuracies.append(b_metrics["accuracy"])
            except Exception as exc:
                print(f"  Baseline seed {seed} failed: {exc}")

        if baseline_accuracies:
            baseline_stats = compute_run_statistics(baseline_accuracies)
            significance_test = compute_t_test_p_value(
                mesa_accuracies, baseline_accuracies
            )

    # --- Build report ---
    report: Dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mesa_version": "0.5.1",
        "seeds_run": seed_list,
        "seeds_completed": len(mesa_accuracies),
        "multi_hop_graph_enabled": True,
        "config_path": args.config,
        "accuracy_statistics": acc_stats,
        "latency_statistics_ms": lat_stats,
        "per_seed_results": all_seed_metrics,
        "summary": (
            f"MESA Accuracy: {acc_stats['formatted_str']}% "
            f"across {len(mesa_accuracies)} seeds "
            f"(mean ± std, n={len(mesa_accuracies)})."
        ),
    }

    if baseline_stats:
        report["baseline_accuracy_statistics"] = baseline_stats
    if significance_test:
        report["significance_test"] = significance_test
        report["summary"] += (
            f" vs Baseline: p={significance_test['p_value_approx']:.4f} "
            f"({'significant' if significance_test['is_significant'] else 'not significant'})."
        )

    # Save
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # --- Print summary ---
    print(f"\n{'='*60}")
    print("=== REPRODUCIBILITY REPORT SUMMARY ===")
    print(f"{'='*60}")
    print(
        f"Accuracy across {len(mesa_accuracies)} seeds: {acc_stats['formatted_str']}%"
    )
    print(f"Average Latency: {lat_stats['formatted_str']} ms")
    if significance_test:
        print(
            f"vs Baseline: t={significance_test['t_stat']:.4f}, "
            f"p={significance_test['p_value_approx']:.4f} "
            f"({'SIGNIFICANT' if significance_test['is_significant'] else 'NOT SIGNIFICANT'})"
        )
    print(f"Report saved to: {args.output}")
    print()
    print("To reproduce these results:")
    print(
        f"  python scripts/reproduce_benchmark.py --config {args.config} --seeds {args.seeds}"
    )


if __name__ == "__main__":
    main()
