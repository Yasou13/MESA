from typing import Any, Dict, List

from pydantic import BaseModel, Field


class MemoryContext(BaseModel):
    id: str = Field(..., description="Unique identifier for the context piece.")
    text: str = Field(..., description="The actual context/memory text to be ingested.")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Optional metadata for the context."
    )


class BenchmarkQuestion(BaseModel):
    id: str = Field(..., description="Unique identifier for the question.")
    query: str = Field(..., description="The question text to ask the target system.")
    ground_truth: str = Field(..., description="The expected correct answer.")
    expected_context_ids: List[str] = Field(
        default_factory=list,
        description="List of MemoryContext IDs that the system MUST retrieve to answer correctly.",
    )
    evaluation_strategy: str = Field(
        "exact_match",
        description="Evaluation strategy to use: 'exact_match', 'llm_judge', 'regex'.",
    )


class BenchmarkScenario(BaseModel):
    id: str = Field(..., description="Unique identifier for the scenario.")
    name: str = Field(..., description="Name of the scenario.")
    description: str = Field("", description="Description of what this scenario tests.")
    contexts: List[MemoryContext] = Field(
        ..., description="List of contexts to ingest into memory."
    )
    questions: List[BenchmarkQuestion] = Field(
        ..., description="List of questions to evaluate after ingestion."
    )
