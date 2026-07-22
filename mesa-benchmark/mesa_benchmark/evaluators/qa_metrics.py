"""Deterministic Full-QA exact-match and token-F1 metrics."""

import re
import string
from collections import Counter
from collections.abc import Callable


def normalize_answer(value: str) -> str:
    """Apply the standard lower/punctuation/article/whitespace normalization."""
    lowered = value.lower()
    without_punctuation = "".join(
        character for character in lowered if character not in string.punctuation
    )
    without_articles = re.sub(r"\b(a|an|the)\b", " ", without_punctuation)
    return " ".join(without_articles.split())


def exact_match(prediction: str, ground_truth: str) -> float:
    return float(normalize_answer(prediction) == normalize_answer(ground_truth))


def token_f1(prediction: str, ground_truth: str) -> float:
    prediction_tokens = normalize_answer(prediction).split()
    truth_tokens = normalize_answer(ground_truth).split()
    if not prediction_tokens or not truth_tokens:
        return float(prediction_tokens == truth_tokens)
    common = Counter(prediction_tokens) & Counter(truth_tokens)
    overlap = sum(common.values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(prediction_tokens)
    recall = overlap / len(truth_tokens)
    return 2 * precision * recall / (precision + recall)


def best_reference_score(
    prediction: str,
    references: list[str],
    scorer: Callable[[str, str], float],
) -> float:
    if not references:
        return 0.0
    return max(scorer(prediction, reference) for reference in references)
