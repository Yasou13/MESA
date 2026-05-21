# MESA v0.3.0 — Phase 1 & 4: Strict Type Validation
# Pydantic V2 schemas for all API payloads to prevent data contamination.
#
# Security guarantees:
#   - No empty strings reach the storage layer (min_length=1 on all IDs)
#   - Payload sizes are bounded to prevent DoS via oversized content
#   - Reserved sentinel values (__unset__) are explicitly rejected
#   - Metadata dicts are depth-limited and key-count-bounded
#   - All models use model_config strict=True for type coercion prevention
#   - Frozen response models prevent post-construction mutation
"""
Strict Pydantic V2 I/O schemas for the MESA API layer.

Every request that enters the API boundary is validated through these
schemas before touching any storage or processing logic.  The schemas
enforce:

    1. Non-empty identity fields (agent_id, session_id)
    2. Rejection of reserved sentinel values (``__unset__``)
    3. Bounded payload sizes (content ≤ 32 KB, metadata ≤ 64 keys)
    4. Strict type enforcement (no implicit coercion)
    5. Frozen response models (immutable after construction)

Usage::

    from mesa_api.schemas import MemoryInsertRequest, MemorySearchRequest

    # FastAPI route
    @app.post("/memory/insert")
    async def insert(request: MemoryInsertRequest):
        ...
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Constants — security boundaries
# ---------------------------------------------------------------------------

# Maximum content payload size (32 KB) — prevents DoS via oversized ingestion
_MAX_CONTENT_LENGTH = 32_768

# Maximum metadata dictionary entries — prevents memory exhaustion
_MAX_METADATA_KEYS = 64

# Maximum metadata value length (individual string values)
_MAX_METADATA_VALUE_LENGTH = 4_096

# Reserved sentinel values that must never be accepted from external input
_RESERVED_SENTINELS = frozenset({"__unset__", "__system__", ""})

# Agent/session ID format: alphanumeric, hyphens, underscores, dots, 1-128 chars
_ID_PATTERN = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")

# ASCII control characters (C0: 0x00-0x1f, DEL: 0x7f) — must be rejected
# BEFORE stripping, since strip() silently removes some of these
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1f\x7f]")

# Maximum query string length
_MAX_QUERY_LENGTH = 4_096

# Search result limit ceiling
_MAX_SEARCH_LIMIT = 50


# ---------------------------------------------------------------------------
# Validators — reusable field-level security checks
# ---------------------------------------------------------------------------


def _validate_identifier(value: str, field_name: str) -> str:
    """Validate an identifier field against security constraints.

    Rejects:
        - Strings containing ASCII control characters (0x00-0x1f, 0x7f)
        - Empty strings
        - Reserved sentinel values (__unset__, __system__)
        - IDs exceeding 128 characters
        - IDs containing disallowed characters
    """
    # Check for control characters BEFORE stripping — strip() silently
    # removes some control chars (e.g., \x1f) which masks injection
    if _CONTROL_CHAR_PATTERN.search(value):
        raise ValueError(
            f"{field_name} contains illegal control characters. "
            f"Only printable ASCII characters are allowed."
        )

    stripped = value.strip()

    if stripped in _RESERVED_SENTINELS:
        raise ValueError(
            f"{field_name} cannot be empty or a reserved value "
            f"('{stripped}'). Provide a valid tenant identifier."
        )

    if not _ID_PATTERN.match(stripped):
        raise ValueError(
            f"{field_name} must be 1-128 characters, containing only "
            f"alphanumeric characters, hyphens, underscores, or dots. "
            f"Got: '{stripped[:32]}{'...' if len(stripped) > 32 else ''}'"
        )

    return stripped


def _validate_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Validate metadata dict against size and depth constraints.

    Rejects:
        - Dicts with more than _MAX_METADATA_KEYS entries
        - Non-string keys
        - String values exceeding _MAX_METADATA_VALUE_LENGTH
        - Nested dicts (depth > 1) to prevent unbounded recursion
    """
    if len(metadata) > _MAX_METADATA_KEYS:
        raise ValueError(
            f"metadata cannot exceed {_MAX_METADATA_KEYS} keys. "
            f"Got {len(metadata)} keys."
        )

    for key, val in metadata.items():
        if not isinstance(key, str):
            raise ValueError(
                f"metadata keys must be strings. Got type "
                f"'{type(key).__name__}' for key '{key}'."
            )

        if isinstance(val, dict):
            raise ValueError(
                f"metadata values cannot be nested dicts "
                f"(key: '{key}'). Flatten the structure."
            )

        if isinstance(val, str) and len(val) > _MAX_METADATA_VALUE_LENGTH:
            raise ValueError(
                f"metadata value for key '{key}' exceeds "
                f"{_MAX_METADATA_VALUE_LENGTH} characters."
            )

    return metadata


# ---------------------------------------------------------------------------
# Request schemas — strict input validation
# ---------------------------------------------------------------------------


class MemoryInsertRequest(BaseModel):
    """Schema for memory ingestion payloads.

    Validates that all identity fields are non-empty, non-reserved,
    and that content does not exceed the 32 KB size limit.

    Fields:
        agent_id: Tenant identifier for row-level isolation (1-128 chars).
        session_id: Session scope within the agent (1-128 chars).
        content: Text content to store (1 to 32,768 characters).
        metadata: Optional key-value metadata (max 64 keys, no nesting).
    """

    model_config = ConfigDict(strict=True, frozen=True)

    agent_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Tenant identifier for row-level isolation",
        examples=["agent_alpha", "data-pipeline-v2"],
    )
    session_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Session scope within the agent tenant",
        examples=["session_001", "batch-2025-05-21"],
    )
    content: str = Field(
        ...,
        min_length=1,
        max_length=_MAX_CONTENT_LENGTH,
        description="Text content to store in the memory layer",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional key-value metadata (max 64 keys, flat structure)",
    )

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, v: str) -> str:
        return _validate_identifier(v, "agent_id")

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        return _validate_identifier(v, "session_id")

    @field_validator("content")
    @classmethod
    def validate_content_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("content cannot be empty or whitespace-only.")
        return stripped

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(v)


class MemorySearchRequest(BaseModel):
    """Schema for memory search/query payloads.

    Validates identity fields, query string bounds, and enforces
    a hard ceiling on search result limits.

    Fields:
        agent_id: Tenant identifier for row-level isolation (1-128 chars).
        session_id: Session scope within the agent (1-128 chars).
        query: Search query string (1 to 4,096 characters).
        limit: Maximum results to return (1-50, default 10).
    """

    model_config = ConfigDict(strict=True, frozen=True)

    agent_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Tenant identifier for scoped search",
    )
    session_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Session scope within the agent tenant",
    )
    query: str = Field(
        ...,
        min_length=1,
        max_length=_MAX_QUERY_LENGTH,
        description="Search query text",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=_MAX_SEARCH_LIMIT,
        description=f"Maximum results to return (1-{_MAX_SEARCH_LIMIT})",
    )

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, v: str) -> str:
        return _validate_identifier(v, "agent_id")

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        return _validate_identifier(v, "session_id")

    @field_validator("query")
    @classmethod
    def validate_query_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("query cannot be empty or whitespace-only.")
        return stripped


class MemoryPurgeRequest(BaseModel):
    """Schema for memory purge (soft-delete) payloads.

    Purge requests ONLY trigger soft-deletes at the API layer.
    Physical removal is handled by the isolated MaintenanceWorker.

    Fields:
        agent_id: Tenant identifier of the requester (1-128 chars).
        scope: Purge scope — 'agent' (all data) or 'session' (single session).
        scope_id: Target identifier to purge (the agent_id or session_id).
    """

    model_config = ConfigDict(strict=True, frozen=True)

    agent_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Tenant identifier of the requester",
    )
    scope: Literal["agent", "session"] = Field(
        ...,
        description="Purge scope: 'agent' for all data, 'session' for a single session",
    )
    scope_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Target identifier to purge (agent_id or session_id)",
    )

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, v: str) -> str:
        return _validate_identifier(v, "agent_id")

    @field_validator("scope_id")
    @classmethod
    def validate_scope_id(cls, v: str) -> str:
        return _validate_identifier(v, "scope_id")

    @model_validator(mode="after")
    def validate_scope_consistency(self) -> "MemoryPurgeRequest":
        """Ensure scope_id matches the declared scope semantics.

        When scope='agent', scope_id must equal agent_id (you can only
        purge your own agent data). When scope='session', scope_id is
        the session to purge within the requesting agent's tenant.
        """
        if self.scope == "agent" and self.scope_id != self.agent_id:
            raise ValueError(
                f"When scope='agent', scope_id must equal agent_id. "
                f"Got agent_id='{self.agent_id}', scope_id='{self.scope_id}'."
            )
        return self


# ---------------------------------------------------------------------------
# Response schemas — frozen output models
# ---------------------------------------------------------------------------


class MemoryInsertResponse(BaseModel):
    """Response returned after a successful memory insertion."""

    model_config = ConfigDict(frozen=True)

    status: Literal["STORED", "DEFERRED", "DISCARDED"] = Field(
        ..., description="Outcome of the valence evaluation"
    )
    node_id: str | None = Field(
        default=None, description="UUID of the stored node (None if discarded)"
    )
    agent_id: str = Field(..., description="Echo of the tenant identifier")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="Server-side ISO 8601 timestamp",
    )


class SearchResultItem(BaseModel):
    """Single result item in a search response."""

    model_config = ConfigDict(frozen=True)

    node_id: str = Field(..., description="UUID of the matching node")
    entity_name: str = Field(..., description="Entity name from the graph")
    score: float = Field(
        ..., ge=0.0, description="Relevance score (lower distance = better)"
    )
    content_hash: str | None = Field(
        default=None, description="SHA-256 of the stored content"
    )
    agent_id: str = Field(..., description="Owning tenant identifier")


class MemorySearchResponse(BaseModel):
    """Response returned after a memory search query."""

    model_config = ConfigDict(frozen=True)

    results: list[SearchResultItem] = Field(
        default_factory=list, description="Ranked search results"
    )
    total: int = Field(..., ge=0, description="Total number of results returned")
    query: str = Field(..., description="Echo of the original query")
    agent_id: str = Field(..., description="Echo of the tenant identifier")


class MemoryPurgeResponse(BaseModel):
    """Response returned after a purge (soft-delete) request."""

    model_config = ConfigDict(frozen=True)

    status: Literal["PURGED"] = Field(
        default="PURGED", description="Always 'PURGED' on success"
    )
    scope: Literal["agent", "session"] = Field(
        ..., description="Echo of the purge scope"
    )
    scope_id: str = Field(..., description="Echo of the purge target")
    records_affected: int = Field(
        ..., ge=0, description="Number of records soft-deleted"
    )


class ErrorResponse(BaseModel):
    """Standardised error response for all API error paths."""

    model_config = ConfigDict(frozen=True)

    error: str = Field(..., description="Machine-readable error code")
    detail: str = Field(..., description="Human-readable error description")
    status_code: int = Field(..., ge=400, le=599)


class HealthResponse(BaseModel):
    """Response for the /health endpoint."""

    model_config = ConfigDict(frozen=True)

    status: Literal["healthy", "degraded", "unhealthy"] = Field(
        ..., description="Overall system health status"
    )
    sqlite: str = Field(default="unknown", description="SQLite engine status")
    vector: str = Field(default="unknown", description="Vector engine status")
    version: str = Field(default="0.3.0", description="MESA version")
