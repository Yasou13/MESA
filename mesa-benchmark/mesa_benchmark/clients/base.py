from abc import ABC, abstractmethod
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from ..datasets.schemas import BenchmarkQuestion, MemoryContext


class BenchmarkResponse(BaseModel):
    """Standardized response from any target memory system."""

    answer_text: str = Field(
        ..., description="The actual text answer returned by the system."
    )
    retrieved_context_ids: List[str] = Field(
        default_factory=list, description="IDs of contexts retrieved by the system."
    )
    latency_ms: float = Field(
        ..., description="Time taken to return the answer in milliseconds."
    )
    token_usage: Dict[str, int] = Field(
        default_factory=dict,
        description="Number of tokens used, e.g. {'prompt': 150, 'completion': 50}.",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Any other system-specific metadata."
    )


class AbstractBenchmarkClient(ABC):
    """
    Abstract Base Class that all target memory systems MUST implement.
    This enforces the 'Apple-to-Apple' rule.
    """

    @abstractmethod
    def initialize(self, config_params: Dict[str, Any]) -> None:
        """
        Initializes the client with given parameters (e.g., API keys, URLs).
        """
        pass

    @abstractmethod
    def clear_memory(self) -> None:
        """
        Completely flushes the memory of the target system to ensure isolated test runs.
        """
        pass

    @abstractmethod
    def add_memory(self, context: MemoryContext) -> Dict[str, Any]:
        """
        Ingests a piece of context into the memory system.
        Should return a dictionary containing at least 'latency_ms'.
        """
        pass

    @abstractmethod
    def answer(self, question: BenchmarkQuestion) -> BenchmarkResponse:
        """
        Queries the target system for the answer to the given question.
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """
        Cleanly shuts down the client and releases any acquired resources (e.g., temporary directories, connection pools).
        """
        pass
