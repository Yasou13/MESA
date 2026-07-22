import argparse
import sys

from .core.config import load_config
from .core.preflight import (
    ollama_preflight,
    print_json,
    validate_config,
    validate_config_and_dataset,
)
from .core.runner import BenchmarkRunner
from .core.suite import check_suite, run_suite, sync_suite, verify_results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mesa-benchmark")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run one benchmark configuration")
    run_parser.add_argument("--config", "-c", required=True)
    run_parser.add_argument("--seed", type=int)
    run_parser.add_argument("--results-root")
    run_parser.add_argument("--max-scenarios", type=int)

    for name in ("config-check", "dataset-check"):
        check_parser = subparsers.add_parser(name)
        check_parser.add_argument("--config", "-c", required=True)
        if name == "dataset-check":
            check_parser.add_argument(
                "--profile", choices=("internal", "publishable"), default="internal"
            )

    preflight_parser = subparsers.add_parser("ollama-preflight")
    preflight_parser.add_argument("--config", "-c", required=True)
    sync_parser = subparsers.add_parser("dataset-sync")
    sync_parser.add_argument("--suite", required=True)
    suite_check_parser = subparsers.add_parser("suite-check")
    suite_check_parser.add_argument("--suite", required=True)
    suite_run_parser = subparsers.add_parser("run-suite")
    suite_run_parser.add_argument("--suite", required=True)
    suite_run_parser.add_argument("--results-root")
    verify_parser = subparsers.add_parser("verify-results")
    verify_parser.add_argument("--bundle", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "config-check":
        print_json(validate_config(args.config))
        return 0
    if args.command == "dataset-check":
        print_json(validate_config_and_dataset(args.config, profile=args.profile))
        return 0
    if args.command == "ollama-preflight":
        print_json(ollama_preflight(load_config(args.config)))
        return 0
    if args.command == "dataset-sync":
        print_json(sync_suite(args.suite))
        return 0
    if args.command == "suite-check":
        print_json(check_suite(args.suite))
        return 0
    if args.command == "run-suite":
        print_json(run_suite(args.suite, args.results_root))
        return 0
    if args.command == "verify-results":
        print_json(verify_results(args.bundle))
        return 0
    if args.command == "run":
        if args.max_scenarios is not None:
            if args.max_scenarios < 1:
                raise SystemExit("--max-scenarios must be positive")
            import os

            os.environ["MESA_MAX_SCENARIOS"] = str(args.max_scenarios)
        outcome = BenchmarkRunner(
            args.config, seed=args.seed, results_root=args.results_root
        ).run()
        print_json(outcome)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
