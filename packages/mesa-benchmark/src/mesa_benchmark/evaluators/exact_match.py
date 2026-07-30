from ..clients.base import BenchmarkResponse
from ..datasets.schemas import BenchmarkQuestion
from .base import BaseEvaluator, EvaluationResult
from .qa_metrics import normalize_answer


class ExactMatchEvaluator(BaseEvaluator):
    """
    A simple evaluator that checks if the exact ground truth string
    is present within the target system's response (case-insensitive substring match).
    """

    def evaluate(
        self, response: BenchmarkResponse, question: BenchmarkQuestion
    ) -> EvaluationResult:
        actual_answer = response.answer_text.strip().lower()

        references = question.reference_answers or [question.ground_truth]
        is_match = any(
            reference.strip().lower() in actual_answer
            for reference in references
            if reference.strip()
        )
        return EvaluationResult(
            score=float(is_match),
            latency_ms=response.latency_ms,
            is_correct=is_match,
            metadata={
                "evaluator_type": "ExactMatchEvaluator",
                "ground_truth": question.ground_truth,
                "reference_answers": references,
                "actual_answer": response.answer_text,
            },
        )


class NormalizedExactMatchEvaluator(BaseEvaluator):
    """Official normalized exact-match over multiple accepted references."""

    def evaluate(
        self, response: BenchmarkResponse, question: BenchmarkQuestion
    ) -> EvaluationResult:
        actual = normalize_answer(response.answer_text)
        references = question.reference_answers or [question.ground_truth]
        is_match = any(
            actual == normalize_answer(reference)
            for reference in references
            if reference.strip()
        )
        return EvaluationResult(
            score=float(is_match),
            latency_ms=response.latency_ms,
            is_correct=is_match,
            metadata={
                "evaluator_type": "NormalizedExactMatchEvaluator",
                "reference_answers": references,
                "actual_answer": response.answer_text,
            },
        )
