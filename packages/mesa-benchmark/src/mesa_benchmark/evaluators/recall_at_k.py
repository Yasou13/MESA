"""Deterministic item-ID Recall@K for recommendation benchmark tracks."""

import re

from ..clients.base import BenchmarkResponse
from ..datasets.schemas import BenchmarkQuestion
from .base import BaseEvaluator, EvaluationResult


class RecallAtKEvaluator(BaseEvaluator):
    def __init__(self, k: int = 5) -> None:
        if k < 1:
            raise ValueError("k must be positive")
        self.k = k

    def evaluate(
        self, response: BenchmarkResponse, question: BenchmarkQuestion
    ) -> EvaluationResult:
        expected = set(question.reference_answers)
        predicted = list(
            dict.fromkeys(re.findall(r"(?<!\w)\d+(?!\w)", response.answer_text))
        )[: self.k]
        score = (
            len(expected.intersection(predicted)) / len(expected) if expected else 0.0
        )
        return EvaluationResult(
            score=score,
            latency_ms=response.latency_ms,
            is_correct=score > 0.0,
            reasoning=f"Recall@{self.k}={score:.4f}",
            metadata={
                "evaluator_type": "RecallAtKEvaluator",
                "k": self.k,
                "predicted_item_ids": predicted,
            },
        )
