# MESA v0.3.0 — Phase 0 CI/CD Gatekeeper
# Parses eval_results.json produced by evals.py and enforces two hard gates:
#
#   Rule 1 (Cost-Efficiency):
#       If any path increases input token cost by >10% over Base,
#       it MUST increase Recall by >=5%.  Otherwise → sys.exit(1).
#
#   Rule 2 (Latency Limit):
#       If any path's mean TTFT exceeds Base mean TTFT + 500ms → sys.exit(1).
#
# Designed for Linux terminal / CI pipeline execution.
# All output is structured for automated log parsing.
"""
CI/CD gatekeeper for the MESA v0.3.0 evaluation pipeline.

Reads the structured JSON output from ``mesa_evals.evals`` and enforces
cost-efficiency and latency SLAs.  Exit code 0 = PASS, 1 = GATE FAILURE.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Gate thresholds — centralised constants for CI/CD tuning
# ---------------------------------------------------------------------------

# Rule 1: Cost-Efficiency
TOKEN_COST_INCREASE_THRESHOLD = 0.10  # 10% input token cost increase
RECALL_IMPROVEMENT_MINIMUM = 0.05  # 5% recall improvement required

# Rule 2: Latency Limit
TTFT_INCREASE_LIMIT_MS = 500.0  # Maximum acceptable TTFT delta vs Base

# Base path name (the comparison baseline for all gates)
BASE_PATH = "Base"

# Default results file location
DEFAULT_RESULTS_PATH = Path(__file__).resolve().parent.parent / "eval_results.json"


# ---------------------------------------------------------------------------
# Gate result structures
# ---------------------------------------------------------------------------


class GateViolation:
    """A single gate rule violation."""

    def __init__(self, rule: str, path: str, message: str, details: dict[str, Any]):
        self.rule = rule
        self.path = path
        self.message = message
        self.details = details

    def __str__(self) -> str:
        return f"GATE_VIOLATION | rule={self.rule} path={self.path} | {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "path": self.path,
            "message": self.message,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Gate enforcement logic
# ---------------------------------------------------------------------------


def _load_results(path: Path) -> dict[str, Any]:
    """Load and validate the evaluation results JSON."""
    if not path.exists():
        print(
            f"GATE_ERROR | Results file not found: {path}",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    required_keys = {"summaries", "results", "evaluation_paths"}
    missing = required_keys - set(data.keys())
    if missing:
        print(
            f"GATE_ERROR | Malformed results file, missing keys: {missing}",
            file=sys.stderr,
        )
        sys.exit(1)

    return data


def _get_base_summary(summaries: dict[str, Any]) -> dict[str, Any]:
    """Extract the Base path summary, or exit if missing."""
    if BASE_PATH not in summaries:
        print(
            f"GATE_ERROR | Base path '{BASE_PATH}' not found in summaries. "
            f"Available paths: {list(summaries.keys())}",
            file=sys.stderr,
        )
        sys.exit(1)
    return summaries[BASE_PATH]


def enforce_cost_efficiency(
    summaries: dict[str, Any],
    base_summary: dict[str, Any],
) -> list[GateViolation]:
    """Rule 1: Cost-Efficiency gate.

    For each non-Base path:
      - Compute input token cost delta vs Base.
      - If delta > 10%, require recall improvement >= 5%.
    """
    violations: list[GateViolation] = []
    base_input_tokens = base_summary["total_input_tokens"]
    base_recall = base_summary["mean_recall"]

    if base_input_tokens == 0:
        print(
            "GATE_WARN | Base path has 0 input tokens; "
            "cost-efficiency gate cannot be evaluated.",
            file=sys.stderr,
        )
        return violations

    for path_name, summary in summaries.items():
        if path_name == BASE_PATH:
            continue

        path_input_tokens = summary["total_input_tokens"]
        token_delta = (path_input_tokens - base_input_tokens) / base_input_tokens
        recall_delta = summary["mean_recall"] - base_recall

        print(
            f"GATE_CHECK | rule=CostEfficiency path={path_name:<20s} "
            f"token_delta={token_delta:>+8.2%} "
            f"recall_delta={recall_delta:>+8.2%}"
        )

        if token_delta > TOKEN_COST_INCREASE_THRESHOLD:
            if recall_delta < RECALL_IMPROVEMENT_MINIMUM:
                violations.append(
                    GateViolation(
                        rule="CostEfficiency",
                        path=path_name,
                        message=(
                            f"Input tokens increased by {token_delta:+.2%} "
                            f"(>{TOKEN_COST_INCREASE_THRESHOLD:.0%} threshold) "
                            f"but recall only improved by {recall_delta:+.2%} "
                            f"(<{RECALL_IMPROVEMENT_MINIMUM:.0%} minimum)."
                        ),
                        details={
                            "base_input_tokens": base_input_tokens,
                            "path_input_tokens": path_input_tokens,
                            "token_delta_pct": round(token_delta * 100, 2),
                            "base_recall": base_recall,
                            "path_recall": summary["mean_recall"],
                            "recall_delta_pct": round(recall_delta * 100, 2),
                            "threshold_token_pct": TOKEN_COST_INCREASE_THRESHOLD * 100,
                            "threshold_recall_pct": RECALL_IMPROVEMENT_MINIMUM * 100,
                        },
                    )
                )
            else:
                print(
                    f"GATE_PASS  | rule=CostEfficiency path={path_name:<20s} "
                    f"token_delta={token_delta:>+8.2%} justified by "
                    f"recall_delta={recall_delta:>+8.2%}"
                )
        else:
            print(
                f"GATE_PASS  | rule=CostEfficiency path={path_name:<20s} "
                f"token_delta={token_delta:>+8.2%} within threshold"
            )

    return violations


def enforce_latency_limit(
    summaries: dict[str, Any],
    base_summary: dict[str, Any],
) -> list[GateViolation]:
    """Rule 2: Latency Limit gate.

    For each non-Base path:
      - If mean TTFT exceeds Base mean TTFT + 500ms → violation.
    """
    violations: list[GateViolation] = []
    base_ttft = base_summary["mean_ttft_ms"]
    ttft_ceiling = base_ttft + TTFT_INCREASE_LIMIT_MS

    for path_name, summary in summaries.items():
        if path_name == BASE_PATH:
            continue

        path_ttft = summary["mean_ttft_ms"]
        ttft_delta = path_ttft - base_ttft

        print(
            f"GATE_CHECK | rule=LatencyLimit   path={path_name:<20s} "
            f"mean_ttft_ms={path_ttft:>10.3f} "
            f"base_ttft_ms={base_ttft:>10.3f} "
            f"delta_ms={ttft_delta:>+10.3f} "
            f"ceiling_ms={ttft_ceiling:>10.3f}"
        )

        if path_ttft > ttft_ceiling:
            violations.append(
                GateViolation(
                    rule="LatencyLimit",
                    path=path_name,
                    message=(
                        f"Mean TTFT {path_ttft:.3f}ms exceeds ceiling "
                        f"{ttft_ceiling:.3f}ms "
                        f"(Base {base_ttft:.3f}ms + {TTFT_INCREASE_LIMIT_MS}ms limit). "
                        f"Delta: {ttft_delta:+.3f}ms."
                    ),
                    details={
                        "base_ttft_ms": base_ttft,
                        "path_ttft_ms": path_ttft,
                        "ttft_delta_ms": round(ttft_delta, 3),
                        "ttft_ceiling_ms": ttft_ceiling,
                        "limit_ms": TTFT_INCREASE_LIMIT_MS,
                    },
                )
            )
        else:
            print(
                f"GATE_PASS  | rule=LatencyLimit   path={path_name:<20s} "
                f"delta_ms={ttft_delta:>+10.3f} within {TTFT_INCREASE_LIMIT_MS}ms limit"
            )

    return violations


# ---------------------------------------------------------------------------
# Main gatekeeper orchestrator
# ---------------------------------------------------------------------------


def run_gatekeeper(results_path: Path | None = None) -> int:
    """Execute all gate rules and return the exit code.

    Returns:
        0 if all gates pass, 1 if any gate is violated.
    """
    path = results_path or DEFAULT_RESULTS_PATH
    print(f"GATE_START | Loading results from {path}")

    data = _load_results(path)
    summaries = data["summaries"]
    base_summary = _get_base_summary(summaries)

    print(
        f"GATE_INFO  | Base path metrics: "
        f"mean_ttft_ms={base_summary['mean_ttft_ms']:.3f} "
        f"input_tokens={base_summary['total_input_tokens']} "
        f"mean_recall={base_summary['mean_recall']:.4f}"
    )
    print(f"GATE_INFO  | Paths under evaluation: {list(summaries.keys())}")
    print("GATE_INFO  | " + "=" * 60)

    # Enforce gates
    all_violations: list[GateViolation] = []

    print("GATE_INFO  | --- Rule 1: Cost-Efficiency ---")
    all_violations.extend(enforce_cost_efficiency(summaries, base_summary))

    print("GATE_INFO  | --- Rule 2: Latency Limit ---")
    all_violations.extend(enforce_latency_limit(summaries, base_summary))

    print("GATE_INFO  | " + "=" * 60)

    # Report
    if all_violations:
        print(
            f"GATE_FAIL  | {len(all_violations)} violation(s) detected:",
            file=sys.stderr,
        )
        for v in all_violations:
            print(f"  ✗ {v}", file=sys.stderr)
            # Also print structured JSON for CI parsing
            print(f"GATE_VIOLATION_JSON | {json.dumps(v.to_dict())}")
        print(f"GATE_RESULT | status=FAIL violations={len(all_violations)}")
        return 1
    else:
        print("GATE_RESULT | status=PASS violations=0")
        return 0


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entrypoint for the MESA CI/CD gatekeeper.

    Usage:
        python -m mesa_evals.gatekeeper
        python -m mesa_evals.gatekeeper --results /path/to/eval_results.json
    """
    parser = argparse.ArgumentParser(
        description="MESA v0.3.0 CI/CD Evaluation Gatekeeper",
    )
    parser.add_argument(
        "--results",
        type=str,
        default=None,
        help=f"Path to eval_results.json (default: {DEFAULT_RESULTS_PATH})",
    )
    args = parser.parse_args()

    results_path = Path(args.results) if args.results else None
    exit_code = run_gatekeeper(results_path)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
