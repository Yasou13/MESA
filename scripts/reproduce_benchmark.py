import argparse
import json
import logging
import math
import os
import sys
from numbers import Real
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "mesa-benchmark"))

from mesa_benchmark.core.runner import BenchmarkRunner
from mesa_benchmark.reports.statistics import (
    compute_paired_test,
    compute_run_statistics,
    compute_t_test_p_value,
)

logging.basicConfig(level=logging.INFO, format="%(name)s - %(levelname)s - %(message)s")

SUMMARY_METRICS = (
    "accuracy",
    "hit_at_1",
    "hit_at_3",
    "hit_at_5",
    "mrr",
    "ndcg",
    "avg_latency_ms",
    "p95_latency_ms",
    "p99_latency_ms",
)


def _numeric_metric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _summarize_metric(runs: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    values: list[float] = []
    excluded_seeds: list[int] = []
    for run in runs:
        value = _numeric_metric(run["metrics"].get(metric))
        if value is None:
            excluded_seeds.append(int(run["seed"]))
        else:
            values.append(value)
    if not values:
        return {
            "mean": None,
            "std": None,
            "se": None,
            "ci_95": None,
            "min": None,
            "max": None,
            "n": 0,
            "formatted_str": "N/A",
            "status": "N/A",
            "available_seed_count": 0,
            "excluded_seed_count": len(excluded_seeds),
            "excluded_seeds": excluded_seeds,
        }
    summary = compute_run_statistics(values)
    summary.update(
        status="available" if not excluded_seeds else "partial",
        available_seed_count=len(values),
        excluded_seed_count=len(excluded_seeds),
        excluded_seeds=excluded_seeds,
    )
    return summary


def _run_system(
    config_path: str, seeds: list[int], results_root: str
) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    for seed in seeds:
        logging.info("Running %s with seed=%d", config_path, seed)
        runner = BenchmarkRunner(config_path, seed=seed, results_root=results_root)
        try:
            outcome = runner.run()
            runs.append(
                {
                    "seed": seed,
                    "status": "success",
                    "run_id": outcome["run_id"],
                    "results_file": outcome["results_file"],
                    "report_file": outcome["report_file"],
                    "metrics": outcome["metrics"],
                }
            )
        except Exception as exc:
            logging.error("Seed %d failed: %s", seed, exc)
            runs.append({"seed": seed, "status": "failed", "error": str(exc)})

    summaries: dict[str, Any] = {}
    successful = [run for run in runs if run["status"] == "success"]
    for metric in SUMMARY_METRICS:
        summaries[metric] = _summarize_metric(successful, metric)
    return {
        "config": config_path,
        "seeds_run": seeds,
        "seeds_completed": len(successful),
        "runs": runs,
        "summary": summaries,
        "valid": len(successful) == len(seeds),
    }


def _compare(primary: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    comparison: dict[str, Any] = {}
    primary_runs = {
        run["seed"]: run for run in primary["runs"] if run["status"] == "success"
    }
    baseline_runs = {
        run["seed"]: run for run in baseline["runs"] if run["status"] == "success"
    }
    shared_seeds = sorted(set(primary_runs).intersection(baseline_runs))
    for metric in SUMMARY_METRICS:
        paired_values = [
            (
                seed,
                _numeric_metric(primary_runs[seed]["metrics"].get(metric)),
                _numeric_metric(baseline_runs[seed]["metrics"].get(metric)),
            )
            for seed in shared_seeds
        ]
        available = [
            (item[0], item[1], item[2])
            for item in paired_values
            if item[1] is not None and item[2] is not None
        ]
        available_seeds = {item[0] for item in available}
        excluded_seeds = [
            item[0] for item in paired_values if item[0] not in available_seeds
        ]
        values_a = [item[1] for item in available]
        values_b = [item[2] for item in available]
        available_status = "available" if available else "N/A"
        comparison[metric] = {
            "shared_seeds": [item[0] for item in available],
            "excluded_seeds": excluded_seeds,
            "available_seed_count": len(available),
            "excluded_seed_count": len(excluded_seeds),
            "status": available_status,
            "primary": _summarize_metric(
                [{"seed": item[0], "metrics": {metric: item[1]}} for item in available],
                metric,
            ),
            "baseline": _summarize_metric(
                [{"seed": item[0], "metrics": {metric: item[2]}} for item in available],
                metric,
            ),
            "welch_t_test": (
                compute_t_test_p_value(values_a, values_b) if available else None
            ),
        }
    paired_primary, paired_baseline = _aligned_question_scores(
        primary_runs, baseline_runs, shared_seeds
    )
    comparison["same_question_accuracy"] = {
        "primary": compute_run_statistics(paired_primary),
        "baseline": compute_run_statistics(paired_baseline),
        "paired_t_test": compute_paired_test(paired_primary, paired_baseline),
    }
    return comparison


def _aligned_question_scores(
    primary_runs: dict[int, dict[str, Any]],
    baseline_runs: dict[int, dict[str, Any]],
    shared_seeds: list[int],
) -> tuple[list[float], list[float]]:
    """Align primary/baseline correctness on identical seed/query keys."""

    def load(path: str, seed: int) -> dict[tuple[int, int, str, str], float]:
        rows: dict[tuple[int, int, str, str], float] = {}
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                key = (
                    seed,
                    int(row["iteration"]),
                    str(row["scenario_id"]),
                    str(row["question_id"]),
                )
                rows[key] = float(bool(row.get("is_correct")))
        return rows

    primary_scores: list[float] = []
    baseline_scores: list[float] = []
    for seed in shared_seeds:
        left = load(primary_runs[seed]["results_file"], seed)
        right = load(baseline_runs[seed]["results_file"], seed)
        for key in sorted(set(left).intersection(right)):
            primary_scores.append(left[key])
            baseline_scores.append(right[key])
    return primary_scores, baseline_scores


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run reproducible MESA benchmarks with real per-seed metrics."
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--baseline-config")
    parser.add_argument("--seeds", default="42,43,44,45,46")
    parser.add_argument("--max-scenarios", type=int)
    parser.add_argument("--output", default="reproducibility_report.json")
    parser.add_argument("--results-root", default="results")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        seeds = [int(item.strip()) for item in args.seeds.split(",") if item.strip()]
    except ValueError as exc:
        raise SystemExit("--seeds must be comma-separated integers") from exc
    if not seeds:
        raise SystemExit("at least one seed is required")
    if args.max_scenarios is not None:
        if args.max_scenarios < 1:
            raise SystemExit("--max-scenarios must be positive")
        os.environ["MESA_MAX_SCENARIOS"] = str(args.max_scenarios)

    primary = _run_system(args.config, seeds, args.results_root)
    report: dict[str, Any] = {
        "schema_version": 3,
        "primary": primary,
        "valid": primary["valid"],
    }
    if args.baseline_config:
        baseline = _run_system(args.baseline_config, seeds, args.results_root)
        report["baseline"] = baseline
        report["comparison"] = _compare(primary, baseline)
        report["valid"] = bool(report["valid"] and baseline["valid"])

    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
    logging.info("Reproducibility report written to %s", args.output)
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
