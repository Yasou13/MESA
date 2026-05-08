"""
P0-A: Pydantic V2 schemas for structured batch extraction output.

These schemas are passed to LLM adapters via the existing ``schema`` kwarg on
``complete()`` / ``acomplete()``, enforcing structured JSON array responses
without any adapter modifications.

- OllamaAdapter: Routes to ``outlines.generate.json(llm, schema)`` for
  grammar-constrained decoding.
- ClaudeAdapter: Calls ``schema.model_validate_json(text)`` for post-hoc
  Pydantic validation.
"""

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ExtractedTriplet(BaseModel):
    """Single knowledge graph triplet extracted from one record."""

    record_index: int = Field(
        ...,
        ge=0,
        description="Zero-based index mapping back to the input record position",
    )
    head: str = Field(..., min_length=1, max_length=256)
    relation: str = Field(..., min_length=1, max_length=256)
    tail: str = Field(..., min_length=1, max_length=256)
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Model self-reported extraction confidence",
    )

    @field_validator("head", "relation", "tail", mode="before")
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        if isinstance(v, str):
            return v.strip()
        return v


class BatchExtractionResponse(BaseModel):
    """Root schema: array of triplets for a multi-record batch."""

    triplets: list[ExtractedTriplet] = Field(
        ...,
        min_length=1,
        description="One triplet per input record, indexed by record_index",
    )
