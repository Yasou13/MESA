"""
Statistical significance and variance analysis utilities for benchmark reporting.
Computes Mean ± Std across multi-seed runs and Student's t-test p-value significance.
"""

import math
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
        # Approximate 95% confidence interval multiplier (approx 1.96 for large n, t-score approximation for small n)
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


def compute_t_test_p_value(sample_a: List[float], sample_b: List[float]) -> Dict[str, Any]:
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
