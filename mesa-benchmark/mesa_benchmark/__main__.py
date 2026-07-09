import argparse
import logging
import sys

from .core.runner import BenchmarkRunner

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="MESA Benchmark Suite v4")
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default="config.yaml",
        help="Path to the benchmark configuration YAML file.",
    )

    args = parser.parse_args()

    try:
        runner = BenchmarkRunner(config_path=args.config)
        runner.run()
    except Exception as e:
        logging.error(f"Execution failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
