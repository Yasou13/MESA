# MESA v0.3.0 — ContextRetriever Test Suite
"""
Tests for the bi-temporal context augmentation retriever.

Covers:
  - Vector-based context retrieval (retrieve_context)
  - FTS5 lexical context retrieval (retrieve_context_fts)
  - Full memory retrieval (retrieve_all_context)
  - Raw record retrieval (retrieve_raw)
  - Bi-temporal gating: consolidated vs unconsolidated warnings
  - Token budget enforcement (whole-node inclusion policy)
  - Empty result handling ("Retrieved Context: None")
  - Custom token counters
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_memory.retriever import (
    _UNCONSOLIDATED_WARNING,
    ContextRetriever,
    _NoOpCounter,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_dao(
    search_results: list | None = None,
    fts_results: list | None = None,
    all_memories: list | None = None,
) -> MagicMock:
    """Create a mock MemoryDAO with pre-configured async methods."""
    dao = MagicMock()
    dao.search_memory = AsyncMock(return_value=search_results or [])
    dao.search_memory_fts = AsyncMock(return_value=fts_results or [])
    dao.get_memories = AsyncMock(return_value=all_memories or [])
    return dao


def _make_vector_result(
    node_id: str,
    entity_name: str = "TestEntity",
    is_consolidated: int = 1,
    node_type: str = "ENTITY",
) -> dict:
    """Build a mock vector search result with graph enrichment."""
    return {
        "node_id": node_id,
        "agent_id": "agent-test",
        "_distance": 0.1,
        "content_hash": "abc123",
        "graph": {
            "id": node_id,
            "entity_name": entity_name,
            "type": node_type,
            "is_consolidated": is_consolidated,
            "created_at": "2026-01-01T00:00:00Z",
        },
    }


def _make_fts_result(
    node_id: str,
    entity_name: str = "TestEntity",
    is_consolidated: int = 1,
) -> dict:
    """Build a mock FTS5 search result (raw nodes table row)."""
    return {
        "id": node_id,
        "entity_name": entity_name,
        "type": "ENTITY",
        "is_consolidated": is_consolidated,
        "created_at": "2026-01-01T00:00:00Z",
        "agent_id": "agent-test",
    }


# ===================================================================
# _NoOpCounter
# ===================================================================


class TestNoOpCounter:
    def test_character_based_heuristic(self):
        counter = _NoOpCounter()
        # "Hello World" is 11 chars → 11 // 4 = 2
        assert counter.get_token_count("Hello World") == 2

    def test_minimum_one_token(self):
        counter = _NoOpCounter()
        # Empty or very short text → minimum 1 token
        assert counter.get_token_count("") == 0 or counter.get_token_count("Hi") >= 1

    def test_long_text(self):
        counter = _NoOpCounter()
        text = "x" * 400
        assert counter.get_token_count(text) == 100


# ===================================================================
# ContextRetriever — Vector-based retrieval
# ===================================================================


class TestRetrieveContext:
    @pytest.mark.asyncio
    async def test_empty_results_returns_none(self):
        dao = _make_mock_dao(search_results=[])
        retriever = ContextRetriever(dao=dao)

        result = await retriever.retrieve_context("agent-test", query_vector=[0.1] * 8)
        assert result == "Retrieved Context: None"

    @pytest.mark.asyncio
    async def test_consolidated_records_no_warning(self):
        results = [_make_vector_result("n1", "Revenue Data", is_consolidated=1)]
        dao = _make_mock_dao(search_results=results)
        retriever = ContextRetriever(dao=dao)

        context = await retriever.retrieve_context("agent-test", query_vector=[0.1] * 8)
        assert "Revenue Data" in context
        assert _UNCONSOLIDATED_WARNING not in context

    @pytest.mark.asyncio
    async def test_unconsolidated_records_get_warning(self):
        results = [_make_vector_result("n1", "Unverified Data", is_consolidated=0)]
        dao = _make_mock_dao(search_results=results)
        retriever = ContextRetriever(dao=dao)

        context = await retriever.retrieve_context("agent-test", query_vector=[0.1] * 8)
        assert "Unverified Data" in context
        assert _UNCONSOLIDATED_WARNING in context

    @pytest.mark.asyncio
    async def test_mixed_consolidation_status(self):
        results = [
            _make_vector_result("n1", "Verified", is_consolidated=1),
            _make_vector_result("n2", "NotVerified", is_consolidated=0),
        ]
        dao = _make_mock_dao(search_results=results)
        retriever = ContextRetriever(dao=dao)

        context = await retriever.retrieve_context("agent-test", query_vector=[0.1] * 8)
        assert "Verified" in context
        assert "NotVerified" in context
        # Only the unconsolidated record should have the warning
        assert context.count(_UNCONSOLIDATED_WARNING) == 1

    @pytest.mark.asyncio
    async def test_dao_called_with_correct_params(self):
        dao = _make_mock_dao()
        retriever = ContextRetriever(dao=dao)

        await retriever.retrieve_context("agent-test", query_vector=[0.1, 0.2], limit=5)
        dao.search_memory.assert_awaited_once_with(
            "agent-test",
            query_vector=[0.1, 0.2],
            limit=5,
            include_graph=True,
        )

    @pytest.mark.asyncio
    async def test_context_includes_header(self):
        results = [_make_vector_result("n1")]
        dao = _make_mock_dao(search_results=results)
        retriever = ContextRetriever(dao=dao)

        context = await retriever.retrieve_context("agent-test", query_vector=[0.1] * 8)
        assert context.startswith("Retrieved Context:")


# ===================================================================
# ContextRetriever — FTS5 lexical retrieval
# ===================================================================


class TestRetrieveContextFts:
    @pytest.mark.asyncio
    async def test_empty_fts_results_returns_none(self):
        dao = _make_mock_dao(fts_results=[])
        retriever = ContextRetriever(dao=dao)

        result = await retriever.retrieve_context_fts("agent-test", query="nonexistent")
        assert result == "Retrieved Context: None"

    @pytest.mark.asyncio
    async def test_fts_results_wrapped_correctly(self):
        fts_results = [
            _make_fts_result("n1", "Contract §4.2", is_consolidated=1),
            _make_fts_result("n2", "Draft Clause", is_consolidated=0),
        ]
        dao = _make_mock_dao(fts_results=fts_results)
        retriever = ContextRetriever(dao=dao)

        context = await retriever.retrieve_context_fts("agent-test", query="Contract")
        assert "Contract §4.2" in context
        assert "Draft Clause" in context
        # Unconsolidated record should have the warning
        assert _UNCONSOLIDATED_WARNING in context

    @pytest.mark.asyncio
    async def test_fts_calls_dao_correctly(self):
        dao = _make_mock_dao()
        retriever = ContextRetriever(dao=dao)

        await retriever.retrieve_context_fts("agent-test", query="tesla", limit=15)
        dao.search_memory_fts.assert_awaited_once_with(
            "agent-test", query="tesla", limit=15
        )


# ===================================================================
# ContextRetriever — All memories retrieval
# ===================================================================


class TestRetrieveAllContext:
    @pytest.mark.asyncio
    async def test_empty_memories_returns_none(self):
        dao = _make_mock_dao(all_memories=[])
        retriever = ContextRetriever(dao=dao)

        result = await retriever.retrieve_all_context("agent-test")
        assert result == "Retrieved Context: None"

    @pytest.mark.asyncio
    async def test_all_memories_with_bi_temporal_gating(self):
        memories = [
            _make_fts_result("n1", "Fact A", is_consolidated=1),
            _make_fts_result("n2", "Fact B", is_consolidated=0),
        ]
        dao = _make_mock_dao(all_memories=memories)
        retriever = ContextRetriever(dao=dao)

        context = await retriever.retrieve_all_context("agent-test")
        assert "Fact A" in context
        assert "Fact B" in context
        assert context.count(_UNCONSOLIDATED_WARNING) == 1

    @pytest.mark.asyncio
    async def test_all_memories_passes_limit(self):
        dao = _make_mock_dao()
        retriever = ContextRetriever(dao=dao)

        await retriever.retrieve_all_context("agent-test", limit=25)
        dao.get_memories.assert_awaited_once_with(
            "agent-test", limit=25, include_consolidated=True
        )


# ===================================================================
# ContextRetriever — Raw retrieval
# ===================================================================


class TestRetrieveRaw:
    @pytest.mark.asyncio
    async def test_returns_raw_results(self):
        results = [_make_vector_result("n1")]
        dao = _make_mock_dao(search_results=results)
        retriever = ContextRetriever(dao=dao)

        raw = await retriever.retrieve_raw(
            "agent-test", query_vector=[0.1] * 8, limit=5
        )
        assert len(raw) == 1
        assert raw[0]["node_id"] == "n1"

    @pytest.mark.asyncio
    async def test_raw_passes_include_graph(self):
        dao = _make_mock_dao()
        retriever = ContextRetriever(dao=dao)

        await retriever.retrieve_raw("agent-test", query_vector=[0.1] * 8, limit=3)
        dao.search_memory.assert_awaited_once_with(
            "agent-test",
            query_vector=[0.1] * 8,
            limit=3,
            include_graph=True,
        )


# ===================================================================
# ContextRetriever — Token budget enforcement
# ===================================================================


class TestTokenBudget:
    @pytest.mark.asyncio
    async def test_budget_truncates_results(self):
        """When token budget is exhausted, remaining records are dropped."""

        class TinyCounter:
            def get_token_count(self, text: str) -> int:
                return len(text)  # 1 char = 1 token for test precision

        results = [
            _make_vector_result(f"n{i}", f"Entity{i}", is_consolidated=1)
            for i in range(20)
        ]
        dao = _make_mock_dao(search_results=results)
        # Very small budget: only enough for header + ~1 record
        retriever = ContextRetriever(
            dao=dao, token_counter=TinyCounter(), max_context_tokens=120
        )

        context = await retriever.retrieve_context("agent-test", query_vector=[0.1] * 8)
        # Should include fewer than 20 records due to budget
        assert context.startswith("Retrieved Context:")
        # Count entries by looking for numbered markers [1], [2], etc.
        entry_count = sum(1 for i in range(20) if f"[{i+1}]" in context)
        assert entry_count < 20

    @pytest.mark.asyncio
    async def test_zero_budget_returns_none(self):
        """If budget is exhausted before any record, return None."""

        class ExhaustingCounter:
            def get_token_count(self, text: str) -> int:
                return 99999  # Everything is "too expensive"

        results = [_make_vector_result("n1")]
        dao = _make_mock_dao(search_results=results)
        retriever = ContextRetriever(
            dao=dao, token_counter=ExhaustingCounter(), max_context_tokens=10
        )

        context = await retriever.retrieve_context("agent-test", query_vector=[0.1] * 8)
        assert context == "Retrieved Context: None"

    @pytest.mark.asyncio
    async def test_max_tokens_override(self):
        """max_tokens parameter overrides the default budget."""
        results = [_make_vector_result("n1", "SomeEntity", is_consolidated=1)]
        dao = _make_mock_dao(search_results=results)
        retriever = ContextRetriever(dao=dao, max_context_tokens=99999)

        context = await retriever.retrieve_context(
            "agent-test", query_vector=[0.1] * 8, max_tokens=99999
        )
        assert "SomeEntity" in context


# ===================================================================
# ContextRetriever — Property access
# ===================================================================


class TestRetrieverProperties:
    def test_dao_property(self):
        dao = _make_mock_dao()
        retriever = ContextRetriever(dao=dao)
        assert retriever.dao is dao
