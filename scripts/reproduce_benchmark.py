import argparse
import json
import logging
import sys
from pathlib import Path

# Add mesa-benchmark to python path
sys.path.insert(0, str(Path(__file__).parent.parent / "mesa-benchmark"))

from mesa_benchmark.core.config import load_config
from mesa_benchmark.core.runner import BenchmarkRunner

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def main():
    parser = argparse.ArgumentParser(description="Reproduce MESA Benchmark with multiple seeds.")
    parser.add_argument("--config", type=str, required=True, help="Path to the primary config.")
    parser.add_argument("--baseline-config", type=str, help="Path to the baseline config for comparison.")
    parser.add_argument("--seeds", type=str, default="42,43,44,45,46", help="Comma-separated list of seeds.")
    parser.add_argument("--max-scenarios", type=int, help="Limit number of scenarios (for quick testing).")
    parser.add_argument("--output", type=str, default="reproducibility_report.json", help="Output JSON report.")

    args = parser.parse_args()
    seeds = [int(s.strip()) for s in args.seeds.split(",")]

    results = {"seeds_run": seeds, "seeds_completed": 0, "runs": []}
    
    for seed in seeds:
        logging.info(f"--- Running seed {seed} ---")
        try:
            cfg = load_config(args.config)
            cfg.seed = seed
            runner = BenchmarkRunner(config_path=args.config)
            runner.config = cfg
            runner.run()
            results["seeds_completed"] += 1
            results["runs"].append({"seed": seed, "status": "success"})
        except Exception as e:
            logging.error(f"Failed on seed {seed}: {e}")
            results["runs"].append({"seed": seed, "status": "failed", "error": str(e)})

    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)
        
    logging.info(f"Done. Report written to {args.output}")


if __name__ == "__main__":
    main()
