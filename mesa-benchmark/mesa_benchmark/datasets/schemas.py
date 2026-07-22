from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field, model_validator

EvaluationStrategy = Literal[
    "exact_match",
    "normalized_exact_match",
    "substring_match",
    "regex",
    "llm_judge",
    "multi_model_judge",
    "rubric_judge",
    "recall_at_5",
]


class MemoryContext(BaseModel):
    id: str = Field(..., description="Unique identifier for the context piece.")
    text: str = Field(..., description="The actual context/memory text to be ingested.")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Optional metadata for the context."
    )


class BenchmarkQuestion(BaseModel):
    id: str = Field(..., description="Unique identifier for the question.")
    query: str = Field(..., description="The question text to ask the target system.")
    reference_answers: List[str] = Field(
        default_factory=list, description="One or more acceptable reference answers."
    )
    rubric: List[str] = Field(
        default_factory=list, description="Atomic criteria used by rubric judges."
    )
    category: str | None = None
    difficulty: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    supporting_context_ids: List[str] = Field(
        default_factory=list, description="Relevant context identifiers."
    )
    required_context_groups: List[List[str]] = Field(
        default_factory=list,
        description="Evidence groups; every group needs at least one retrieved member.",
    )
    forbidden_context_ids: List[str] = Field(
        default_factory=list,
        description="Outdated or otherwise disallowed evidence identifiers.",
    )
    ground_truth: str = Field(
        "", description="Legacy alias for the first reference answer."
    )
    expected_context_ids: List[str] = Field(
        default_factory=list,
        description="List of MemoryContext IDs that the system MUST retrieve to answer correctly.",
    )
    evaluation_strategy: EvaluationStrategy = Field(
        "exact_match",
        description="Typed evaluation strategy used by the harness.",
    )

    @model_validator(mode="before")
    @classmethod
    def lift_legacy_metadata(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        metadata = data.get("metadata")
        if isinstance(metadata, dict):
            for field in ("category", "difficulty", "rubric"):
                if data.get(field) in (None, "", []):
                    lifted = metadata.get(field)
                    if lifted not in (None, "", []):
                        data[field] = lifted
        rubric = data.get("rubric")
        if isinstance(rubric, str):
            data["rubric"] = [rubric] if rubric.strip() else []
        references = data.get("reference_answers")
        if isinstance(references, str):
            data["reference_answers"] = [references] if references.strip() else []
        return data

    @model_validator(mode="after")
    def normalize_v2_aliases(self) -> "BenchmarkQuestion":
        references = [item.strip() for item in self.reference_answers if item.strip()]
        legacy_answer = self.ground_truth.strip()
        if not references and legacy_answer:
            references = [legacy_answer]
        supporting = list(dict.fromkeys(self.supporting_context_ids))
        legacy_supporting = list(dict.fromkeys(self.expected_context_ids))
        if not supporting:
            supporting = legacy_supporting
        if not legacy_supporting:
            legacy_supporting = supporting
        rubric = [item.strip() for item in self.rubric if item.strip()]
        if not references and not rubric:
            raise ValueError(
                "question requires at least one reference answer or rubric"
            )
        self.reference_answers = references
        self.ground_truth = legacy_answer or (references[0] if references else "")
        self.supporting_context_ids = supporting
        self.expected_context_ids = legacy_supporting
        self.rubric = rubric
        return self


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
