from abc import ABC, abstractmethod
from typing import Any, Dict

from pydantic import BaseModel, Field

from ..clients.base import BenchmarkResponse
from ..datasets.schemas import BenchmarkQuestion


class EvaluationResult(BaseModel):
    """The result of evaluating a client's response against the ground truth."""

    score: float = Field(
        ..., description="Score between 0.0 and 1.0 (e.g., 1.0 for perfect match)."
    )
    latency_ms: float = Field(
        ..., description="Latency of the response (passed through from client)."
    )
    is_correct: bool = Field(
        ...,
        description="Boolean indicating if the answer is considered 'correct' based on the threshold.",
    )
    reasoning: str = Field(
        "",
        description="Explanation of the evaluation decision (especially for LLM-as-a-Judge).",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Detailed evaluation breakdown or logs."
    )


class BaseEvaluator(ABC):
    """
    Abstract Base Class for all evaluators.
    Evaluators are stateless and only compare the expected vs actual output.
    """

    @abstractmethod
    def evaluate(
        self, response: BenchmarkResponse, question: BenchmarkQuestion
    ) -> EvaluationResult:
        """
        Evaluates the response and returns a score.
        """
        pass
