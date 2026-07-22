#!/usr/bin/env python3
import argparse
import logging
import os

# Adjust path so we can import mesa_benchmark
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mesa_benchmark.core.runner import BenchmarkRunner
from mesa_benchmark.metrics.calculator import calculate_metrics_from_jsonl

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("ComparisonModule")


def main():
    parser = argparse.ArgumentParser(
        description="MESA Automated Comparison Leaderboard"
    )
    parser.add_argument(
        "--configs",
        nargs="+",
        default=[
            "config.yaml",
            "config_mem0.yaml",
            "config_letta.yaml",
            "config_zep.yaml",
        ],
        help="List of configuration files to run and compare.",
    )
    parser.add_argument(
        "--judge-model",
        help="Exact model tag to set as BENCHMARK_JUDGE_MODEL for every system.",
    )

    args = parser.parse_args()

    results_data = []

    for config_path in args.configs:
        if not os.path.exists(config_path):
            logger.warning(f"Config file not found: {config_path}. Skipping.")
            continue

        if args.judge_model:
            os.environ["BENCHMARK_JUDGE_MODEL"] = args.judge_model

        logger.info(f"=== Starting benchmark for {config_path} ===")
        try:
            runner = BenchmarkRunner(config_path=config_path)
            # We just do setup to get the suite name early, and load client
            print("[DEBUG] run_comp: before runner.setup()", flush=True)
            runner.setup()
            print("[DEBUG] run_comp: after runner.setup()", flush=True)
            suite_name = runner.config.suite_name

            print("[DEBUG] run_comp: before runner.run()", flush=True)
            runner.run()
            print("[DEBUG] run_comp: after runner.run()", flush=True)

            # Extract results
            res_file = runner.state_manager.state.results_file
            metrics = calculate_metrics_from_jsonl(res_file)

            results_data.append(
                {
                    "suite": suite_name,
                    "status": "Success",
                    "metrics": metrics,
                    "error": None,
                }
            )
            logger.info(f"Successfully evaluated {suite_name}")

        except ImportError as e:
            logger.warning(f"Dependencies missing for {config_path}: {e}")
            results_data.append(
                {
                    "suite": config_path,
                    "status": "Skipped (Missing Deps)",
                    "metrics": None,
                    "error": str(e),
                }
            )
        except Exception as e:
            logger.error(f"Failed to run {config_path}: {e}")
            results_data.append(
                {
                    "suite": config_path,
                    "status": "Failed",
                    "metrics": None,
                    "error": str(e),
                }
            )
    # Generate LEADERBOARD.md
    generate_leaderboard(results_data)


def generate_leaderboard(results_data):
    lines = [
        "# 🏆 MESA Benchmark Leaderboard",
        "",
        "This table represents the comparative performance of MESA against baseline memory architectures.",
        "All systems were evaluated against the exact same multi-hop reasoning dataset with an identical LLM Judge.",
        "",
        "| System / Suite | Status | Accuracy | Hit@5 | nDCG@5 | MRR | Avg Latency | Token Efficiency |",
        "|:---|:---|:---|:---|:---|:---|:---|:---|",
    ]

    # Sort results_data: successes first, sorted by accuracy descending, then nDCG descending
    def get_sort_key(item):
        if item["status"] != "Success" or not item["metrics"]:
            return (-1.0, -1.0)
        return (item["metrics"].accuracy, item["metrics"].ndcg)

    sorted_results = sorted(results_data, key=get_sort_key, reverse=True)

    for item in sorted_results:
        suite = item["suite"]
        status = item["status"]
        if status == "Success":
            m = item["metrics"]
            acc = f"%{m.accuracy * 100:.1f}"
            hit5 = f"%{m.hit_at_5 * 100:.1f}"
            ndcg = f"%{m.ndcg * 100:.1f}"
            mrr = f"%{m.mrr * 100:.1f}"
            lat = f"{m.avg_latency_ms:.1f}ms"
            te = f"{m.token_efficiency:.1f}" if m.token_efficiency else "N/A"

            lines.append(
                f"| **{suite}** | ✅ {status} | {acc} | {hit5} | {ndcg} | {mrr} | {lat} | {te} |"
            )
        else:
            _ = item["error"].replace("\n", " ").replace("|", " ")
            lines.append(f"| **{suite}** | ❌ {status} | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "---",
            "*Note: Skipped systems indicate missing external dependencies (e.g., `mem0ai` or `letta` not installed).* ",
        ]
    )

    out_path = Path("LEADERBOARD.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info(f"Leaderboard generated successfully at {out_path.absolute()}")


if __name__ == "__main__":
    main()
