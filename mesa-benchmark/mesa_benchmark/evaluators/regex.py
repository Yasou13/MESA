import re

from ..clients.base import BenchmarkResponse
from ..datasets.schemas import BenchmarkQuestion
from .base import BaseEvaluator, EvaluationResult


class RegexEvaluator(BaseEvaluator):
    """
    Evaluator that checks if the ground truth pattern exists in the response
    using a regular expression search.
    """

    def evaluate(
        self, response: BenchmarkResponse, question: BenchmarkQuestion
    ) -> EvaluationResult:
        pattern = question.ground_truth.strip()
        actual_answer = response.answer_text.strip()

        try:
            is_match = bool(re.search(pattern, actual_answer, re.IGNORECASE))
        except re.error:
            # If the regex is invalid, default to False
            is_match = False

        score = 1.0 if is_match else 0.0

        return EvaluationResult(
            score=score,
            latency_ms=response.latency_ms,
            is_correct=is_match,
            metadata={
                "evaluator_type": "RegexEvaluator",
                "ground_truth": question.ground_truth,
                "actual_answer": response.answer_text,
            },
        )
