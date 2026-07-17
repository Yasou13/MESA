"""Tests for MesaClientAdapter and ingestion_worker functions.

Covers initialization, add_memory, answer, clear_memory, close flows
for MesaClientAdapter and cold-path ingestion helper functions.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../mesa-benchmark"))
)

from mesa_benchmark.clients.mesa_client import BenchmarkAccessControl, MesaClientAdapter

from mesa_workers.ingestion_worker import (
    _get_extraction_prompt,
    _hash_embedding_sync,
    _parse_llm_triplet_response,
    _sanitize_llm_json,
    process_cold_path,
)

# ─── MesaClientAdapter tests ──────────────────────────────────────────


class TestMesaClientAdapterDefaults:
    """Verify default attribute values without calling initialize()."""

    def test_defaults(self):
        adapter = MesaClientAdapter()
        assert adapter.enable_multi_hop is True
        assert adapter.enable_rerank is False
        assert adapter.top_n == 5
        assert adapter.timeout_s == 30.0
        assert adapter.memory_dao is None
        assert adapter.retriever is None
        assert adapter.context_id_map == {}

    def test_config_override(self):
        adapter = MesaClientAdapter()
        # Patch all heavy I/O that initialize() triggers
        with (
            patch("mesa_benchmark.clients.mesa_client.AsyncEngine") as m_sql,
            patch("mesa_benchmark.clients.mesa_client.VectorEngine") as m_vec,
            patch("mesa_benchmark.clients.mesa_client.KuzuGraphProvider") as m_graph,
            patch(
                "mesa_benchmark.clients.mesa_client.initialize_schema",
                new_callable=AsyncMock,
            ),
            patch("mesa_benchmark.clients.mesa_client.kuzu_initialize_schema"),
            patch("mesa_benchmark.clients.mesa_client.AdapterFactory"),
            patch("mesa_benchmark.clients.mesa_client.QueryAnalyzer"),
            patch("mesa_benchmark.clients.mesa_client.HybridRetriever"),
        ):
            # Make the awaitable mocks return coroutines
            m_sql.return_value.initialize = AsyncMock()
            m_vec.return_value.initialize = AsyncMock()
            m_graph.return_value.initialize = AsyncMock()
            mock_dao = MagicMock()
            mock_dao.initialize = AsyncMock()
            with patch(
                "mesa_benchmark.clients.mesa_client.MemoryDAO", return_value=mock_dao
            ):
                adapter.initialize(
                    {
                        "enable_multi_hop": False,
                        "top_n": 20,
                        "enable_rerank": True,
                        "timeout_s": 60.0,
                    }
                )

        assert adapter.enable_multi_hop is False
        assert adapter.top_n == 20
        assert adapter.enable_rerank is True
        assert adapter.timeout_s == 60.0


class TestMesaClientAdapterClose:
    """Verify close() cleans up temp_dir."""

    def test_close_cleans_tempdir(self):
        adapter = MesaClientAdapter()
        mock_temp = MagicMock()
        adapter.temp_dir = mock_temp
        adapter.close()
        mock_temp.cleanup.assert_called_once()

    def test_close_without_tempdir(self):
        adapter = MesaClientAdapter()
        adapter.temp_dir = None
        # Should not raise
        adapter.close()


class TestBenchmarkAccessControl:
    """BenchmarkAccessControl always grants access."""

    @pytest.mark.asyncio
    async def test_always_true(self):
        ac = BenchmarkAccessControl()
        assert await ac.check_access("any", "any", "read") is True
        assert await ac.check_access("", "", "write") is True


# ─── ingestion_worker helper function tests ──────────────────────────


class TestHashEmbeddingSync:
    """_hash_embedding_sync produces a deterministic, fixed-dim vector."""

    def test_deterministic(self):
        v1 = _hash_embedding_sync("hello world", dim=8)
        v2 = _hash_embedding_sync("hello world", dim=8)
        assert v1 == v2
        assert len(v1) == 8

    def test_different_inputs_differ(self):
        v1 = _hash_embedding_sync("aaa", dim=8)
        v2 = _hash_embedding_sync("bbb", dim=8)
        assert v1 != v2

    def test_custom_dim(self):
        v = _hash_embedding_sync("test", dim=16)
        assert len(v) == 16


class TestGetExtractionPrompt:
    """_get_extraction_prompt returns a non-empty prompt containing the text."""

    def test_contains_text(self):
        prompt = _get_extraction_prompt("The cat sat on the mat")
        assert "The cat sat on the mat" in prompt
        assert len(prompt) > 50  # Should include instructions too


class TestSanitizeLlmJson:
    """_sanitize_llm_json strips markdown fences and fixes common issues."""

    def test_strip_markdown_fence(self):
        raw = '```json\n[{"subject": "A", "predicate": "B", "object": "C"}]\n```'
        result = _sanitize_llm_json(raw)
        assert "```" not in result
        assert '"subject"' in result

    def test_plain_json_passthrough(self):
        raw = '[{"subject": "A"}]'
        result = _sanitize_llm_json(raw)
        assert result.strip() == raw.strip()


class TestParseLlmTripletResponse:
    """_parse_llm_triplet_response extracts triplets from LLM output."""

    def test_valid_json_array(self):
        raw = '[{"subject": "Alice", "predicate": "knows", "object": "Bob"}]'
        result = _parse_llm_triplet_response(raw)
        assert len(result) >= 1
        # Function normalizes subject->head, predicate->relation, object->tail
        assert result[0]["head"] == "Alice"
        assert result[0]["relation"] == "knows"
        assert result[0]["tail"] == "Bob"

    def test_empty_response(self):
        result = _parse_llm_triplet_response("")
        assert result == []

    def test_invalid_json_returns_empty(self):
        result = _parse_llm_triplet_response("not valid json at all {{{")
        assert result == []


class TestProcessColdPath:
    """process_cold_path: tests the main entry point with mocked DAO."""

    @pytest.mark.asyncio
    async def test_skip_when_log_not_found(self):
        """Should return early if raw_log doesn't exist."""
        mock_dao = AsyncMock()
        mock_dao.get_raw_log.return_value = None

        # Should not raise
        await process_cold_path(log_id=999, agent_id="test", dao=mock_dao)
        mock_dao.get_raw_log.assert_awaited_once_with("test", 999)
        # Should NOT call update_raw_log_status since log was not found
        mock_dao.update_raw_log_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skip_when_status_not_deferred(self):
        """Should skip processing when status is not DEFERRED."""
        mock_dao = AsyncMock()
        mock_dao.get_raw_log.return_value = {"status": "processed", "payload": {}}

        await process_cold_path(log_id=1, agent_id="test", dao=mock_dao)
        # Should NOT transition status since it's already processed
        mock_dao.update_raw_log_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reject_missing_content(self):
        """Should reject entries with missing content field."""
        mock_dao = AsyncMock()
        mock_dao.get_raw_log.return_value = {
            "status": "DEFERRED",
            "payload": {"agent_id": "test_agent", "content": ""},
        }

        await process_cold_path(log_id=2, agent_id="test", dao=mock_dao)
        # Should mark as rejected due to missing content
        mock_dao.update_raw_log_status.assert_awaited()
        call_args = mock_dao.update_raw_log_status.call_args
        assert call_args[0][2] == "rejected"
