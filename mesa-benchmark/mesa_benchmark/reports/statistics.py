"""
Statistical significance and variance analysis utilities for benchmark reporting.
Computes Mean ± Std across multi-seed runs and Student's t-test p-value significance.
"""

import math
import random
from typing import Any, Dict, List


def compute_run_statistics(values: List[float]) -> Dict[str, Any]:
    """
    Computes summary statistics (mean, std, min, max, 95% CI) for a metric across multiple seed runs.

    Args:
        values: List of metric values across seeds.

    Returns:
        Dict containing mean, std, se, ci_95, min, max, n, formatted_str.
    """
    if not values:
        return {
            "mean": 0.0,
            "std": 0.0,
            "se": 0.0,
            "ci_95": 0.0,
            "min": 0.0,
            "max": 0.0,
            "n": 0,
            "formatted_str": "N/A",
        }

    n = len(values)
    mean = sum(values) / n

    if n > 1:
        variance = sum((x - mean) ** 2 for x in values) / (n - 1)
        std = math.sqrt(variance)
        se = std / math.sqrt(n)
        try:
            from scipy import stats as sp_stats

            t_mult = float(sp_stats.t.ppf(0.975, n - 1))
        except ImportError:
            t_mult = 1.96 if n >= 30 else 2.262
        ci_95 = se * t_mult
    else:
        std = 0.0
        se = 0.0
        ci_95 = 0.0

    formatted_str = f"{mean:.2f} ± {std:.2f}" if n > 1 else f"{mean:.2f}"

    return {
        "mean": round(mean, 4),
        "std": round(std, 4),
        "se": round(se, 4),
        "ci_95": round(ci_95, 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "n": n,
        "formatted_str": formatted_str,
    }


def compute_t_test_p_value(
    sample_a: List[float], sample_b: List[float]
) -> Dict[str, Any]:
    """
    Computes independent two-sample Welch's t-test t-statistic and approximate p-value.

    Args:
        sample_a: Metric scores from Model/System A across n seeds/runs.
        sample_b: Metric scores from Model/System B across m seeds/runs.

    Returns:
        Dict containing t_stat, p_value_approx, is_significant (p < 0.05), mean_diff.
    """
    if not sample_a or not sample_b or len(sample_a) < 2 or len(sample_b) < 2:
        return {
            "t_stat": 0.0,
            "p_value_approx": 1.0,
            "is_significant": False,
            "mean_diff": 0.0,
            "note": "Insufficient sample size (need n >= 2 for both samples).",
        }

    stats_a = compute_run_statistics(sample_a)
    stats_b = compute_run_statistics(sample_b)

    mean_a, std_a, n_a = stats_a["mean"], stats_a["std"], stats_a["n"]
    mean_b, std_b, n_b = stats_b["mean"], stats_b["std"], stats_b["n"]

    mean_diff = mean_a - mean_b
    se_diff = math.sqrt((std_a**2 / n_a) + (std_b**2 / n_b))

    if se_diff == 0.0:
        t_stat = 0.0
        p_val = 1.0 if mean_diff == 0.0 else 0.001
    else:
        t_stat = mean_diff / se_diff
        # Degrees of freedom approximation (Welch-Satterthwaite equation)
        num = ((std_a**2 / n_a) + (std_b**2 / n_b)) ** 2
        den = ((std_a**2 / n_a) ** 2 / (n_a - 1)) + ((std_b**2 / n_b) ** 2 / (n_b - 1))
        df = max(1.0, num / den if den > 0 else 1.0)

        # Attempt exact two-tailed p-value using scipy.stats if available
        try:
            from scipy import stats as sp_stats

            p_val = float(sp_stats.t.sf(abs(t_stat), df) * 2.0)
        except ImportError:
            # Approximate two-tailed p-value using normal erfc approximation
            x = abs(t_stat)
            p_val = math.erfc(x / math.sqrt(2))

    return {
        "t_stat": round(t_stat, 4),
        "p_value_approx": round(p_val, 4),
        "is_significant": p_val < 0.05,
        "mean_diff": round(mean_diff, 4),
    }


def compute_paired_test(sample_a: List[float], sample_b: List[float]) -> Dict[str, Any]:
    """Compute a paired t-test and CI from aligned observations."""
    if len(sample_a) != len(sample_b):
        raise ValueError("paired samples must have the same length")
    differences = [left - right for left, right in zip(sample_a, sample_b)]
    summary = compute_run_statistics(differences)
    if len(differences) < 2:
        return {
            "n": len(differences),
            "mean_difference": summary["mean"],
            "ci_95": summary["ci_95"],
            "t_stat": 0.0,
            "p_value": 1.0,
            "is_significant": False,
        }
    se = float(summary["se"])
    mean_difference = float(summary["mean"])
    if se == 0:
        t_stat = (
            0.0 if mean_difference == 0 else math.copysign(math.inf, mean_difference)
        )
        p_value = 1.0 if mean_difference == 0 else 0.0
    else:
        t_stat = mean_difference / se
        try:
            from scipy import stats as sp_stats

            p_value = float(sp_stats.t.sf(abs(t_stat), len(differences) - 1) * 2.0)
        except ImportError:
            p_value = math.erfc(abs(t_stat) / math.sqrt(2))
    return {
        "n": len(differences),
        "mean_difference": round(mean_difference, 6),
        "ci_95": summary["ci_95"],
        "t_stat": round(t_stat, 4),
        "p_value": round(p_value, 6),
        "is_significant": p_value < 0.05,
    }


def paired_bootstrap_ci(
    sample_a: List[float],
    sample_b: List[float],
    *,
    confidence: float = 0.95,
    resamples: int = 10_000,
    seed: int = 42,
) -> Dict[str, Any]:
    """Question-level paired bootstrap interval for the mean score delta."""
    if len(sample_a) != len(sample_b):
        raise ValueError("paired samples must have the same length")
    if not sample_a:
        raise ValueError("paired bootstrap requires at least one observation")
    if not 0 < confidence < 1 or resamples < 100:
        raise ValueError("invalid confidence or resample count")
    differences = [left - right for left, right in zip(sample_a, sample_b)]
    rng = random.Random(seed)
    n = len(differences)
    sampled_means = sorted(
        sum(differences[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(resamples)
    )
    alpha = (1.0 - confidence) / 2.0
    lower_index = max(0, int(alpha * resamples))
    upper_index = min(resamples - 1, int((1.0 - alpha) * resamples) - 1)
    return {
        "n": n,
        "mean_difference": sum(differences) / n,
        "confidence": confidence,
        "ci_lower": sampled_means[lower_index],
        "ci_upper": sampled_means[upper_index],
        "resamples": resamples,
        "seed": seed,
    }


def mcnemar_test(outcomes_a: List[bool], outcomes_b: List[bool]) -> Dict[str, Any]:
    """Exact two-sided McNemar test for paired binary question outcomes."""
    if len(outcomes_a) != len(outcomes_b):
        raise ValueError("paired outcomes must have the same length")
    b = sum(left and not right for left, right in zip(outcomes_a, outcomes_b))
    c = sum(right and not left for left, right in zip(outcomes_a, outcomes_b))
    discordant = b + c
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(math.comb(discordant, index) for index in range(min(b, c) + 1)) / (
            2**discordant
        )
        p_value = min(1.0, 2.0 * tail)
    return {
        "n": len(outcomes_a),
        "a_only_correct": b,
        "b_only_correct": c,
        "p_value": p_value,
        "is_significant": p_value < 0.05,
    }


def holm_adjust(p_values: Dict[str, float]) -> Dict[str, float]:
    """Holm-Bonferroni adjustment for a family of metric comparisons."""
    ordered = sorted(p_values.items(), key=lambda item: item[1])
    adjusted: Dict[str, float] = {}
    running = 0.0
    count = len(ordered)
    for index, (name, value) in enumerate(ordered):
        running = max(running, min(1.0, (count - index) * value))
        adjusted[name] = running
    return adjusted
