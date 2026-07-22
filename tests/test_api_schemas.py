# MESA v0.3.0 — Phase 1 & 4: API Schema Test Suite
"""
Tests for strict Pydantic V2 I/O schemas: identity validation, payload
size limits, reserved sentinel rejection, metadata constraints, response
model immutability, and cross-field consistency.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mesa_api.schemas import (
    ErrorResponse,
    HealthResponse,
    MemoryInsertRequest,
    MemoryInsertResponse,
    MemoryPurgeRequest,
    MemoryPurgeResponse,
    MemorySearchRequest,
    MemorySearchResponse,
    SearchResultItem,
)

# ===================================================================
# MemoryInsertRequest
# ===================================================================


class TestMemoryInsertRequest:
    """Validates MemoryInsertRequest input sanitization."""

    def test_valid_minimal(self):
        req = MemoryInsertRequest(
            agent_id="agent_1",
            session_id="sess_001",
            content="Hello, world.",
        )
        assert req.agent_id == "agent_1"
        assert req.session_id == "sess_001"
        assert req.content == "Hello, world."
        assert req.metadata == {}

    def test_valid_with_metadata(self):
        req = MemoryInsertRequest(
            agent_id="agent_1",
            session_id="sess_001",
            content="Test content",
            metadata={"source": "unit_test", "priority": 1},
        )
        assert req.metadata["source"] == "unit_test"
        assert req.metadata["priority"] == 1

    def test_rejects_empty_agent_id(self):
        with pytest.raises(ValidationError) as exc_info:
            MemoryInsertRequest(
                agent_id="",
                session_id="sess_001",
                content="content",
            )
        assert "agent_id" in str(exc_info.value)

    def test_rejects_whitespace_agent_id(self):
        with pytest.raises(ValidationError) as exc_info:
            MemoryInsertRequest(
                agent_id="   ",
                session_id="sess_001",
                content="content",
            )
        assert "agent_id" in str(exc_info.value)

    def test_rejects_reserved_sentinel_unset(self):
        with pytest.raises(ValidationError) as exc_info:
            MemoryInsertRequest(
                agent_id="__unset__",
                session_id="sess_001",
                content="content",
            )
        assert "reserved" in str(exc_info.value).lower()

    def test_rejects_reserved_sentinel_system(self):
        with pytest.raises(ValidationError) as exc_info:
            MemoryInsertRequest(
                agent_id="__system__",
                session_id="sess_001",
                content="content",
            )
        assert "reserved" in str(exc_info.value).lower()

    def test_rejects_empty_content(self):
        with pytest.raises(ValidationError):
            MemoryInsertRequest(
                agent_id="agent_1",
                session_id="sess_001",
                content="",
            )

    def test_rejects_whitespace_only_content(self):
        with pytest.raises(ValidationError) as exc_info:
            MemoryInsertRequest(
                agent_id="agent_1",
                session_id="sess_001",
                content="   \n\t  ",
            )
        assert "whitespace" in str(exc_info.value).lower()

    def test_rejects_oversized_content(self):
        with pytest.raises(ValidationError):
            MemoryInsertRequest(
                agent_id="agent_1",
                session_id="sess_001",
                content="x" * 32_769,
            )

    def test_content_at_max_limit_accepted(self):
        req = MemoryInsertRequest(
            agent_id="agent_1",
            session_id="sess_001",
            content="x" * 32_768,
        )
        assert len(req.content) == 32_768

    def test_rejects_special_chars_in_agent_id(self):
        with pytest.raises(ValidationError):
            MemoryInsertRequest(
                agent_id="agent;DROP TABLE",
                session_id="sess_001",
                content="content",
            )

    def test_strips_whitespace_from_content(self):
        req = MemoryInsertRequest(
            agent_id="agent_1",
            session_id="sess_001",
            content="  padded content  ",
        )
        assert req.content == "padded content"

    def test_rejects_nested_metadata(self):
        with pytest.raises(ValidationError) as exc_info:
            MemoryInsertRequest(
                agent_id="agent_1",
                session_id="sess_001",
                content="content",
                metadata={"nested": {"deep": "value"}},
            )
        assert "nested" in str(exc_info.value).lower()

    def test_rejects_excess_metadata_keys(self):
        huge_meta = {f"key_{i}": f"val_{i}" for i in range(65)}
        with pytest.raises(ValidationError) as exc_info:
            MemoryInsertRequest(
                agent_id="agent_1",
                session_id="sess_001",
                content="content",
                metadata=huge_meta,
            )
        assert "64" in str(exc_info.value)

    def test_metadata_at_key_limit_accepted(self):
        meta = {f"key_{i}": f"val_{i}" for i in range(64)}
        req = MemoryInsertRequest(
            agent_id="agent_1",
            session_id="sess_001",
            content="content",
            metadata=meta,
        )
        assert len(req.metadata) == 64

    def test_rejects_oversized_metadata_value(self):
        with pytest.raises(ValidationError):
            MemoryInsertRequest(
                agent_id="agent_1",
                session_id="sess_001",
                content="content",
                metadata={"big": "x" * 4_097},
            )

    def test_strict_mode_rejects_int_as_agent_id(self):
        with pytest.raises(ValidationError):
            MemoryInsertRequest(
                agent_id=123,  # type: ignore[arg-type]
                session_id="sess_001",
                content="content",
            )

    def test_frozen_prevents_mutation(self):
        req = MemoryInsertRequest(
            agent_id="agent_1",
            session_id="sess_001",
            content="content",
        )
        with pytest.raises(ValidationError):
            req.content = "mutated"  # type: ignore[misc]

    def test_accepts_dotted_agent_id(self):
        req = MemoryInsertRequest(
            agent_id="org.team.agent-v2",
            session_id="sess_001",
            content="content",
        )
        assert req.agent_id == "org.team.agent-v2"

    def test_rejects_agent_id_exceeding_128_chars(self):
        with pytest.raises(ValidationError):
            MemoryInsertRequest(
                agent_id="a" * 129,
                session_id="sess_001",
                content="content",
            )


# ===================================================================
# MemorySearchRequest
# ===================================================================


class TestMemorySearchRequest:
    """Validates MemorySearchRequest input sanitization."""

    def test_valid_with_defaults(self):
        req = MemorySearchRequest(
            agent_id="agent_1",
            session_id="sess_001",
            query="What is MESA?",
        )
        assert req.limit == 10

    def test_valid_with_custom_limit(self):
        req = MemorySearchRequest(
            agent_id="agent_1",
            session_id="sess_001",
            query="query",
            limit=25,
        )
        assert req.limit == 25

    def test_rejects_limit_above_max(self):
        with pytest.raises(ValidationError):
            MemorySearchRequest(
                agent_id="agent_1",
                session_id="sess_001",
                query="query",
                limit=51,
            )

    def test_rejects_limit_zero(self):
        with pytest.raises(ValidationError):
            MemorySearchRequest(
                agent_id="agent_1",
                session_id="sess_001",
                query="query",
                limit=0,
            )

    def test_rejects_negative_limit(self):
        with pytest.raises(ValidationError):
            MemorySearchRequest(
                agent_id="agent_1",
                session_id="sess_001",
                query="query",
                limit=-5,
            )

    def test_limit_at_max_accepted(self):
        req = MemorySearchRequest(
            agent_id="agent_1",
            session_id="sess_001",
            query="query",
            limit=50,
        )
        assert req.limit == 50

    def test_rejects_empty_query(self):
        with pytest.raises(ValidationError):
            MemorySearchRequest(
                agent_id="agent_1",
                session_id="sess_001",
                query="",
            )

    def test_rejects_whitespace_only_query(self):
        with pytest.raises(ValidationError) as exc_info:
            MemorySearchRequest(
                agent_id="agent_1",
                session_id="sess_001",
                query="   \t\n   ",
            )
        assert "whitespace" in str(exc_info.value).lower()

    def test_rejects_oversized_query(self):
        with pytest.raises(ValidationError):
            MemorySearchRequest(
                agent_id="agent_1",
                session_id="sess_001",
                query="q" * 4_097,
            )

    def test_rejects_reserved_sentinel_session_id(self):
        with pytest.raises(ValidationError) as exc_info:
            MemorySearchRequest(
                agent_id="agent_1",
                session_id="__unset__",
                query="query",
            )
        assert "reserved" in str(exc_info.value).lower()

    def test_strips_query_whitespace(self):
        req = MemorySearchRequest(
            agent_id="agent_1",
            session_id="sess_001",
            query="  trimmed query  ",
        )
        assert req.query == "trimmed query"


# ===================================================================
# MemoryPurgeRequest
# ===================================================================


class TestMemoryPurgeRequest:
    """Validates MemoryPurgeRequest scope logic and identity checks."""

    def test_valid_agent_scope(self):
        req = MemoryPurgeRequest(
            agent_id="agent_1",
            scope="agent",
            scope_id="agent_1",
        )
        assert req.scope == "agent"
        assert req.scope_id == req.agent_id

    def test_valid_session_scope(self):
        req = MemoryPurgeRequest(
            agent_id="agent_1",
            scope="session",
            scope_id="sess_001",
        )
        assert req.scope == "session"

    def test_rejects_invalid_scope_literal(self):
        with pytest.raises(ValidationError):
            MemoryPurgeRequest(
                agent_id="agent_1",
                scope="global",  # type: ignore[arg-type]
                scope_id="anything",
            )

    def test_agent_scope_requires_matching_scope_id(self):
        with pytest.raises(ValidationError) as exc_info:
            MemoryPurgeRequest(
                agent_id="agent_1",
                scope="agent",
                scope_id="agent_2",
            )
        assert "scope_id must equal agent_id" in str(exc_info.value)

    def test_session_scope_allows_different_scope_id(self):
        req = MemoryPurgeRequest(
            agent_id="agent_1",
            scope="session",
            scope_id="any-session-id",
        )
        assert req.scope_id == "any-session-id"

    def test_rejects_reserved_agent_id(self):
        with pytest.raises(ValidationError):
            MemoryPurgeRequest(
                agent_id="__unset__",
                scope="session",
                scope_id="sess_001",
            )

    def test_rejects_empty_scope_id(self):
        with pytest.raises(ValidationError):
            MemoryPurgeRequest(
                agent_id="agent_1",
                scope="session",
                scope_id="",
            )


# ===================================================================
# Response schemas
# ===================================================================


class TestResponseSchemas:
    """Validates response model construction and immutability."""

    def test_insert_response_queued(self):
        resp = MemoryInsertResponse(
            status="queued",
            log_id=123,
            agent_id="agent_1",
        )
        assert resp.status == "queued"
        assert resp.log_id == 123
        assert resp.processing_mode == "async"

    def test_insert_response_requires_durable_log_id(self):
        with pytest.raises(ValidationError):
            MemoryInsertResponse(status="queued", agent_id="agent_1")  # type: ignore[call-arg]

    def test_insert_response_frozen(self):
        resp = MemoryInsertResponse(status="queued", log_id=1, agent_id="agent_1")
        with pytest.raises(ValidationError):
            resp.status = "MUTATED"  # type: ignore[misc]

    def test_search_response(self):
        item = SearchResultItem(
            node_id="n1",
            entity_name="TestEntity",
            score=0.95,
            agent_id="agent_1",
        )
        resp = MemorySearchResponse(
            context="Some context",
            retrieved_nodes=[item],
            metrics={"latency_ms": 10},
            degraded_sources=["graph"],
        )
        assert resp.context == "Some context"
        assert resp.metrics["latency_ms"] == 10
        assert resp.retrieved_nodes[0].entity_name == "TestEntity"

    def test_search_result_rejects_negative_score(self):
        with pytest.raises(ValidationError):
            SearchResultItem(
                node_id="n1",
                entity_name="E",
                score=-0.1,
                agent_id="a",
            )

    def test_purge_response(self):
        resp = MemoryPurgeResponse(deleted_records_count=42)
        assert resp.status == "purged"
        assert resp.deleted_records_count == 42

    def test_error_response(self):
        resp = ErrorResponse(
            error="VALIDATION_ERROR",
            detail="Invalid payload",
            status_code=422,
        )
        assert resp.status_code == 422

    def test_health_response(self):
        resp = HealthResponse(
            status="healthy",
            sqlite="ok",
            vector="ok",
        )
        assert isinstance(resp.version, str)


# ===================================================================
# Cross-cutting concerns
# ===================================================================


class TestCrossCutting:
    """Tests for behaviors that span multiple schema types."""

    def test_all_requests_reject_sql_injection_in_ids(self):
        """Identifiers with SQL special chars are rejected."""
        malicious = "'; DROP TABLE nodes;--"
        for schema_cls in [
            MemoryInsertRequest,
            MemorySearchRequest,
            MemoryPurgeRequest,
        ]:
            with pytest.raises(ValidationError):
                if schema_cls == MemoryInsertRequest:
                    schema_cls(
                        agent_id=malicious,
                        session_id="sess",
                        content="c",
                    )
                elif schema_cls == MemorySearchRequest:
                    schema_cls(
                        agent_id=malicious,
                        session_id="sess",
                        query="q",
                    )
                else:
                    schema_cls(
                        agent_id=malicious,
                        scope="session",
                        scope_id="s",
                    )

    def test_all_requests_reject_unicode_control_chars(self):
        """Identifiers with null bytes / control chars are rejected."""
        for bad_id in ["\x00agent", "agent\x1f", "zer\x00o"]:
            with pytest.raises(ValidationError):
                MemoryInsertRequest(
                    agent_id=bad_id,
                    session_id="sess",
                    content="content",
                )

    def test_json_serialization_roundtrip(self):
        """Schemas can serialize to JSON and deserialize cleanly."""
        req = MemoryInsertRequest(
            agent_id="agent_1",
            session_id="sess_001",
            content="roundtrip test",
            metadata={"key": "value"},
        )
        json_str = req.model_dump_json()
        restored = MemoryInsertRequest.model_validate_json(json_str)
        assert restored.agent_id == req.agent_id
        assert restored.content == req.content
        assert restored.metadata == req.metadata
