"""
Agreement rate and consistency metric calculator.
Computes agreement rate (%) and Cohen's Kappa between Keyword/ExactMatch and LLM-Judge evaluators.
"""

from typing import Any, Dict, List


def compute_agreement(
    scores_a: List[float], scores_b: List[float], threshold: float = 0.5
) -> Dict[str, Any]:
    """
    Computes agreement statistics between two evaluation score lists.

    Args:
        scores_a: First list of scores (e.g., keyword/exact match scores 0.0 - 1.0).
        scores_b: Second list of scores (e.g., LLM judge scores 0.0 - 1.0).
        threshold: Score threshold to classify as correct vs incorrect (default 0.5).

    Returns:
        Dict containing agreement_rate (0.0 to 100.0), cohens_kappa (-1.0 to 1.0),
        and raw contingency counts.
    """
    if not scores_a or not scores_b or len(scores_a) != len(scores_b):
        return {
            "agreement_rate": 0.0,
            "cohens_kappa": 0.0,
            "concordant": 0,
            "total": len(scores_a) if scores_a else 0,
        }

    n = len(scores_a)
    bin_a = [s >= threshold for s in scores_a]
    bin_b = [s >= threshold for s in scores_b]

    # Contingency table counts
    a1_b1 = sum(1 for a, b in zip(bin_a, bin_b) if a and b)
    a1_b0 = sum(1 for a, b in zip(bin_a, bin_b) if a and not b)
    a0_b1 = sum(1 for a, b in zip(bin_a, bin_b) if not a and b)
    a0_b0 = sum(1 for a, b in zip(bin_a, bin_b) if not a and not b)

    concordant = a1_b1 + a0_b0
    observed_agreement = concordant / n

    # Expected agreement by chance
    p_a1 = (a1_b1 + a1_b0) / n
    p_b1 = (a1_b1 + a0_b1) / n
    p_a0 = 1.0 - p_a1
    p_b0 = 1.0 - p_b1

    expected_agreement = (p_a1 * p_b1) + (p_a0 * p_b0)

    if expected_agreement == 1.0:
        kappa = 1.0 if observed_agreement == 1.0 else 0.0
    else:
        kappa = (observed_agreement - expected_agreement) / (1.0 - expected_agreement)

    return {
        "agreement_rate": round(observed_agreement * 100.0, 2),
        "cohens_kappa": round(kappa, 4),
        "concordant": concordant,
        "total": n,
        "contingency_table": {
            "both_correct": a1_b1,
            "only_a_correct": a1_b0,
            "only_b_correct": a0_b1,
            "both_incorrect": a0_b0,
        },
    }
