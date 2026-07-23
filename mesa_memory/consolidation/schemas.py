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

from __future__ import annotations

from typing import Any, Optional
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field, field_validator


class MemoryCandidate(BaseModel):
    """Canonical, retry-stable hand-off record for cognitive ingestion.

    The durable raw log remains the admission/audit source. This model is the
    only shape passed from a worker to Tier-3/projection code, preventing the
    legacy ``id/content`` aliases from omitting the validated payload.
    """

    candidate_id: str = Field(min_length=1)
    mutation_id: str = Field(min_length=1)
    raw_log_id: int = Field(ge=1)
    tenant_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    content_payload: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    evidence_span: str = ""
    source: str = "api"
    performative: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    pipeline_run_id: str | None = None
    extraction_version: str = "v4"
    embedding_model: str | None = None
    embedding_version: str | None = None
    embedding_dimension: int | None = Field(default=None, ge=1)
    created_artifact_ids: list[str] = Field(default_factory=list)

    @classmethod
    def from_raw_log(
        cls,
        *,
        raw_log_id: int,
        agent_id: str,
        session_id: str,
        content_payload: str,
        metadata: dict[str, Any] | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        dataset_id: str | None = None,
        document_id: str | None = None,
        revision_id: str | None = None,
        chunk_id: str | None = None,
        source_ref: str | None = None,
        evidence_span: str = "",
        pipeline_run_id: str | None = None,
    ) -> "MemoryCandidate":
        """Create deterministic IDs so a redelivery cannot duplicate work."""
        tenant = tenant_id or agent_id
        workspace = workspace_id or f"legacy-workspace:{agent_id}"
        dataset = dataset_id or f"legacy-dataset:{agent_id}"
        document = document_id or f"legacy-document:{raw_log_id}"
        revision = revision_id or f"{document}:revision:1"
        chunk = chunk_id or f"{revision}:chunk:0"
        reference = source_ref or f"raw-log:{raw_log_id}"
        identity = f"mesa:v4:{tenant}:{agent_id}:{raw_log_id}"
        return cls(
            candidate_id=str(uuid5(NAMESPACE_URL, f"{identity}:candidate")),
            mutation_id=str(uuid5(NAMESPACE_URL, f"{identity}:mutation")),
            raw_log_id=raw_log_id,
            tenant_id=tenant,
            workspace_id=workspace,
            dataset_id=dataset,
            document_id=document,
            revision_id=revision,
            chunk_id=chunk,
            agent_id=agent_id,
            session_id=session_id,
            content_payload=content_payload,
            source_ref=reference,
            evidence_span=evidence_span,
            metadata=metadata or {},
            pipeline_run_id=pipeline_run_id
            or str(uuid5(NAMESPACE_URL, f"{identity}:pipeline-run")),
        )

    def as_consolidation_record(self) -> dict[str, Any]:
        """Return the compatibility projection consumed by the existing loop."""
        return {
            "cmb_id": self.candidate_id,
            "candidate_id": self.candidate_id,
            "mutation_id": self.mutation_id,
            "raw_log_id": self.raw_log_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "dataset_id": self.dataset_id,
            "document_id": self.document_id,
            "revision_id": self.revision_id,
            "chunk_id": self.chunk_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "content_payload": self.content_payload,
            "source_ref": self.source_ref,
            "evidence_span": self.evidence_span,
            "metadata": self.metadata,
            "source": self.source,
            "performative": self.performative,
            "pipeline_run_id": self.pipeline_run_id,
            "extraction_version": self.extraction_version,
            "embedding_model": self.embedding_model,
            "embedding_version": self.embedding_version,
            "embedding_dimension": self.embedding_dimension,
            "created_artifact_ids": self.created_artifact_ids,
            "tier3_deferred": True,
        }


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
