"""Pydantic models for the MESA V4 wire contract."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mesa_contracts.validation import validate_write_payload


class V4MutationStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    mutation_id: str
    candidate_id: str
    state: str
    failure_class: str | None = None
    rejection_reason: str | None = None
    tier3_audit: dict | None = None
    pipeline_run: dict | None = None
    artifacts: list[dict] = Field(default_factory=list)
    projections: list[dict] = Field(default_factory=list)


class V4DatasetRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=128)
    tenant_name: str | None = Field(default=None, max_length=256)
    workspace_name: str | None = Field(default=None, max_length=256)
    dataset_name: str | None = Field(default=None, max_length=256)


class V4WorkspaceRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    tenant_name: str | None = Field(default=None, max_length=256)
    workspace_name: str | None = Field(default=None, max_length=256)


class V4DocumentRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=128)
    document_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=512)
    external_ref: str | None = Field(default=None, max_length=2048)


class V4RevisionRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=128)
    document_id: str = Field(min_length=1, max_length=256)
    revision_id: str = Field(min_length=1, max_length=256)
    revision_number: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    supersedes_revision_id: str | None = Field(default=None, max_length=256)


class V4SourceChunkRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=128)
    document_id: str = Field(min_length=1, max_length=256)
    revision_id: str = Field(min_length=1, max_length=256)
    chunk_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=512)
    content: str = Field(min_length=1, max_length=32768)
    source_ref: str = Field(min_length=1, max_length=2048)
    revision_number: int = Field(default=1, ge=1)
    chunk_ordinal: int = Field(default=0, ge=0)
    external_ref: str | None = Field(default=None, max_length=2048)
    supersedes_revision_id: str | None = Field(default=None, max_length=256)


class V4SessionStartRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    dataset_ids: list[str] = Field(min_length=1, max_length=64)
    agent_id: str = Field(min_length=1, max_length=128)


class V4MemoryInsertRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    session_id: str = Field(min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=128)
    document_id: str = Field(min_length=1, max_length=256)
    revision_id: str = Field(min_length=1, max_length=256)
    chunk_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=512)
    source_ref: str = Field(min_length=1, max_length=2048)
    content: str = Field(min_length=1, max_length=32768)
    evidence_span: str = Field(default="", max_length=4096)
    revision_number: int = Field(default=1, ge=1)
    chunk_ordinal: int = Field(default=0, ge=0)
    supersedes_revision_id: str | None = Field(default=None, max_length=256)
    metadata: dict = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_write_boundary(self) -> "V4MemoryInsertRequest":
        validate_write_payload(self.content, self.metadata)
        return self


class V4SearchRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    session_id: str = Field(min_length=1, max_length=128)
    dataset_ids: list[str] | None = Field(default=None, max_length=64)
    query: str = Field(min_length=1, max_length=4096)
    limit: int = Field(default=10, ge=1, le=50)
    jurisdiction: str | None = Field(default=None, max_length=256)
    valid_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @model_validator(mode="after")
    def validate_temporal_range(self) -> "V4SearchRequest":
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_from must not be after valid_to")
        return self
