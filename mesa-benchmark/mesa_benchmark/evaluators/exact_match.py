from ..clients.base import BenchmarkResponse
from ..datasets.schemas import BenchmarkQuestion
from .base import BaseEvaluator, EvaluationResult


class ExactMatchEvaluator(BaseEvaluator):
    """
    A simple evaluator that checks if the exact ground truth string
    is present within the target system's response (case-insensitive substring match).
    """

    def evaluate(
        self, response: BenchmarkResponse, question: BenchmarkQuestion
    ) -> EvaluationResult:
        ground_truth = question.ground_truth.strip().lower()
        actual_answer = response.answer_text.strip().lower()

        # Substring exact match
        is_match = ground_truth in actual_answer
        score = 1.0 if is_match else 0.0

        return EvaluationResult(
            score=score,
            latency_ms=response.latency_ms,
            is_correct=is_match,
            metadata={
                "evaluator_type": "ExactMatchEvaluator",
                "ground_truth": question.ground_truth,
                "actual_answer": response.answer_text,
            },
        )
