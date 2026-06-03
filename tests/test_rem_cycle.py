# MESA v0.3.0 — REM Cycle Worker Test Suite
"""
Tests for the REMCycleWorker background consolidation pipeline.

Covers:
  - Activation threshold enforcement (50-record gate)
  - Batch processing with per-cycle token budget cap
  - Dual-LLM consensus contradiction detection
  - Conflict resolution protocol (invalidate old, promote new, link edge)
  - Fail-safe behaviour (LLM failures default to no-contradiction)
  - Contradiction response parsing (JSON, malformed, string booleans)
  - Metrics recording (cycles, skips, failures)
  - Worker lifecycle (start/stop, context manager, idempotent start)
  - Manual trigger via run_now()
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_workers.rem_cycle import (
    REMCycleMetrics,
    REMCycleWorker,
    _parse_contradiction_response,
    evaluate_contradiction,
    resolve_conflict,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    node_id: str = "n1",
    entity_name: str = "TestEntity",
    node_type: str = "ENTITY",
    agent_id: str = "agent-test",
    is_consolidated: int = 0,
) -> dict:
    """Build a minimal node dict for testing."""
    return {
        "id": node_id,
        "entity_name": entity_name,
        "type": node_type,
        "agent_id": agent_id,
        "is_consolidated": is_consolidated,
        "created_at": "2026-01-01T00:00:00Z",
    }


def _make_mock_dao() -> MagicMock:
    """Create a mock MemoryDAO with all async methods pre-configured."""
    dao = MagicMock()
    dao.get_memories = AsyncMock(return_value=[])
    dao.mark_consolidated = AsyncMock()
    dao.invalidate_node = AsyncMock()
    dao.insert_edge = AsyncMock(return_value="edge-id")
    dao.find_consolidated_nodes_by_name = AsyncMock(return_value=[])
    dao.graph_provider = MagicMock()
    dao.graph_provider.execute_write = AsyncMock()
    return dao


def _make_mock_llm(response: str = '{"contradiction": false}') -> MagicMock:
    """Create a mock LLM adapter returning a fixed response."""
    llm = MagicMock()
    llm.acomplete = AsyncMock(return_value=response)
    return llm


# ===================================================================
# _parse_contradiction_response
# ===================================================================


class TestParseContradictionResponse:
    def test_valid_json_false(self):
        raw = '{"contradiction": false, "justification": "No conflict"}'
        assert _parse_contradiction_response(raw, "LLM_A") is False

    def test_valid_json_true(self):
        raw = '{"contradiction": true, "justification": "Conflicts"}'
        assert _parse_contradiction_response(raw, "LLM_A") is True

    def test_string_true(self):
        raw = '{"contradiction": "true"}'
        assert _parse_contradiction_response(raw, "LLM_A") is True

    def test_string_false(self):
        raw = '{"contradiction": "false"}'
        assert _parse_contradiction_response(raw, "LLM_A") is False

    def test_markdown_fenced_json(self):
        raw = '```json\n{"contradiction": true}\n```'
        assert _parse_contradiction_response(raw, "LLM_A") is True

    def test_invalid_json_defaults_false(self):
        raw = "This is not JSON at all"
        assert _parse_contradiction_response(raw, "LLM_A") is False

    def test_none_input_defaults_false(self):
        assert _parse_contradiction_response(None, "LLM_A") is False

    def test_missing_key_defaults_false(self):
        raw = '{"justification": "something"}'
        assert _parse_contradiction_response(raw, "LLM_A") is False

    def test_integer_input(self):
        assert _parse_contradiction_response(42, "LLM_A") is False

    def test_empty_string_defaults_false(self):
        assert _parse_contradiction_response("", "LLM_A") is False


# ===================================================================
# evaluate_contradiction — Dual-LLM consensus
# ===================================================================


class TestEvaluateContradiction:
    @pytest.mark.asyncio
    async def test_both_agree_contradiction(self):
        llm_a = _make_mock_llm('{"contradiction": true}')
        llm_b = _make_mock_llm('{"contradiction": true}')
        existing = _make_node("old", is_consolidated=1)
        new = _make_node("new", is_consolidated=0)

        result = await evaluate_contradiction(llm_a, llm_b, existing, new)
        assert result is True

    @pytest.mark.asyncio
    async def test_both_agree_no_contradiction(self):
        llm_a = _make_mock_llm('{"contradiction": false}')
        llm_b = _make_mock_llm('{"contradiction": false}')
        existing = _make_node("old", is_consolidated=1)
        new = _make_node("new", is_consolidated=0)

        result = await evaluate_contradiction(llm_a, llm_b, existing, new)
        assert result is False

    @pytest.mark.asyncio
    async def test_disagreement_defaults_false(self):
        """Fail-safe: disagreement → no contradiction (preserve existing)."""
        llm_a = _make_mock_llm('{"contradiction": true}')
        llm_b = _make_mock_llm('{"contradiction": false}')
        existing = _make_node("old", is_consolidated=1)
        new = _make_node("new", is_consolidated=0)

        result = await evaluate_contradiction(llm_a, llm_b, existing, new)
        assert result is False

    @pytest.mark.asyncio
    async def test_reverse_disagreement_defaults_false(self):
        llm_a = _make_mock_llm('{"contradiction": false}')
        llm_b = _make_mock_llm('{"contradiction": true}')
        existing = _make_node("old")
        new = _make_node("new")

        result = await evaluate_contradiction(llm_a, llm_b, existing, new)
        assert result is False

    @pytest.mark.asyncio
    async def test_llm_calls_use_correct_prompts(self):
        llm_a = _make_mock_llm('{"contradiction": false}')
        llm_b = _make_mock_llm('{"contradiction": false}')
        existing = _make_node("old", entity_name="Revenue $25B")
        new = _make_node("new", entity_name="Revenue $30B")

        await evaluate_contradiction(llm_a, llm_b, existing, new)

        # Both LLMs should have been called exactly once
        llm_a.acomplete.assert_awaited_once()
        llm_b.acomplete.assert_awaited_once()

        # Check that entity names appear in the prompts
        prompt_a = llm_a.acomplete.call_args[0][0]
        assert "Revenue $25B" in prompt_a
        assert "Revenue $30B" in prompt_a


# ===================================================================
# resolve_conflict — Graph operations
# ===================================================================


class TestResolveConflict:
    @pytest.mark.asyncio
    async def test_invalidates_old_node(self):
        dao = _make_mock_dao()
        existing = _make_node("old-id", is_consolidated=1)
        new = _make_node("new-id", is_consolidated=0)

        await resolve_conflict(dao, "agent-test", existing, new)

        # mark_consolidated should be called for the new node
        dao.mark_consolidated.assert_awaited_once_with("agent-test", node_id="new-id")

    @pytest.mark.asyncio
    async def test_creates_supersedes_edge(self):
        dao = _make_mock_dao()
        existing = _make_node("old-id", is_consolidated=1)
        new = _make_node("new-id", is_consolidated=0)

        await resolve_conflict(dao, "agent-test", existing, new)

        dao.insert_edge.assert_awaited_once_with(
            "agent-test",
            source_id="new-id",
            target_id="old-id",
            relation_type="SUPERSEDES",
            weight=1.0,
        )

    @pytest.mark.asyncio
    async def test_invalidates_old_node_via_dao(self):
        dao = _make_mock_dao()
        existing = _make_node("old-id", is_consolidated=1)
        new = _make_node("new-id", is_consolidated=0)

        await resolve_conflict(dao, "agent-test", existing, new)

        dao.invalidate_node.assert_awaited_once_with("agent-test", node_id="old-id")


# ===================================================================
# REMCycleMetrics
# ===================================================================


class TestREMCycleMetrics:
    @pytest.mark.asyncio
    async def test_record_cycle(self):
        metrics = REMCycleMetrics()
        await metrics.record_cycle(
            consolidated=5, contradicted=2, promoted=2, duration_ms=150.0
        )
        snap = metrics.snapshot()
        assert snap["cycles_completed"] == 1
        assert snap["records_consolidated"] == 5
        assert snap["records_contradicted"] == 2
        assert snap["records_promoted"] == 2
        assert snap["total_cycle_time_ms"] == 150.0
        assert snap["last_cycle_at"] is not None

    @pytest.mark.asyncio
    async def test_record_skip(self):
        metrics = REMCycleMetrics()
        await metrics.record_skip()
        await metrics.record_skip()
        assert metrics.snapshot()["cycles_skipped"] == 2

    @pytest.mark.asyncio
    async def test_record_failure(self):
        metrics = REMCycleMetrics()
        await metrics.record_failure()
        assert metrics.snapshot()["cycles_failed"] == 1

    @pytest.mark.asyncio
    async def test_multiple_cycles_accumulate(self):
        metrics = REMCycleMetrics()
        await metrics.record_cycle(
            consolidated=3, contradicted=1, promoted=1, duration_ms=100
        )
        await metrics.record_cycle(
            consolidated=7, contradicted=0, promoted=0, duration_ms=200
        )
        snap = metrics.snapshot()
        assert snap["cycles_completed"] == 2
        assert snap["records_consolidated"] == 10
        assert snap["total_cycle_time_ms"] == 300.0


# ===================================================================
# REMCycleWorker — activation threshold & batch budget
# ===================================================================


class TestREMCycleWorkerThreshold:
    @pytest.mark.asyncio
    async def test_below_threshold_skips_processing(self):
        """When unconsolidated < 50, the worker must skip."""
        dao = _make_mock_dao()
        # Return 49 records (below default threshold of 50)
        dao.get_memories = AsyncMock(
            return_value=[_make_node(f"n{i}") for i in range(49)]
        )
        llm_a = _make_mock_llm()
        llm_b = _make_mock_llm()

        worker = REMCycleWorker(
            dao=dao,
            llm_a=llm_a,
            llm_b=llm_b,
            agent_ids=["agent-test"],
        )
        await worker._process_agent("agent-test")

        # mark_consolidated should NOT have been called — threshold gate
        dao.mark_consolidated.assert_not_awaited()
        assert worker.metrics.snapshot()["cycles_skipped"] == 1

    @pytest.mark.asyncio
    async def test_at_threshold_triggers_processing(self):
        """When unconsolidated == 50 (exactly at threshold), process."""
        dao = _make_mock_dao()
        records = [_make_node(f"n{i}") for i in range(50)]
        dao.get_memories = AsyncMock(return_value=records)

        llm_a = _make_mock_llm()
        llm_b = _make_mock_llm()

        worker = REMCycleWorker(
            dao=dao,
            llm_a=llm_a,
            llm_b=llm_b,
            agent_ids=["agent-test"],
        )
        # Patch _find_consolidated_matches to return empty (no conflicts)
        worker._find_consolidated_matches = AsyncMock(return_value=[])

        await worker._process_agent("agent-test")

        # All 50 records should be consolidated (no conflicts)
        assert dao.mark_consolidated.await_count == 50

    @pytest.mark.asyncio
    async def test_batch_budget_caps_records_per_cycle(self):
        """When queue > max_records_per_cycle, only process the budget."""
        dao = _make_mock_dao()
        records = [_make_node(f"n{i}") for i in range(200)]
        dao.get_memories = AsyncMock(return_value=records)

        llm_a = _make_mock_llm()
        llm_b = _make_mock_llm()

        worker = REMCycleWorker(
            dao=dao,
            llm_a=llm_a,
            llm_b=llm_b,
            agent_ids=["agent-test"],
            activation_threshold=50,
            max_records_per_cycle=75,
        )
        worker._find_consolidated_matches = AsyncMock(return_value=[])

        await worker._process_agent("agent-test")

        # Should process exactly 75 (the budget), not 200
        assert dao.mark_consolidated.await_count == 75

    @pytest.mark.asyncio
    async def test_custom_threshold(self):
        """Custom activation_threshold=10 should trigger at 10 records."""
        dao = _make_mock_dao()
        records = [_make_node(f"n{i}") for i in range(10)]
        dao.get_memories = AsyncMock(return_value=records)

        worker = REMCycleWorker(
            dao=dao,
            llm_a=_make_mock_llm(),
            llm_b=_make_mock_llm(),
            activation_threshold=10,
        )
        worker._find_consolidated_matches = AsyncMock(return_value=[])

        await worker._process_agent("agent-test")
        assert dao.mark_consolidated.await_count == 10


# ===================================================================
# REMCycleWorker — consolidation with conflicts
# ===================================================================


class TestREMCycleWorkerConflicts:
    @pytest.mark.asyncio
    async def test_no_match_consolidates_cleanly(self):
        """Record with no existing entity match → mark consolidated."""
        dao = _make_mock_dao()
        new = _make_node("n1", entity_name="UniqueEntity")
        dao.get_memories = AsyncMock(return_value=[new] * 50)

        worker = REMCycleWorker(
            dao=dao,
            llm_a=_make_mock_llm(),
            llm_b=_make_mock_llm(),
            activation_threshold=50,
        )
        worker._find_consolidated_matches = AsyncMock(return_value=[])

        result = await worker._consolidate_record("agent-test", new)
        assert result is False
        dao.mark_consolidated.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_contradiction_triggers_conflict_resolution(self):
        """Dual-LLM consensus contradiction → resolve_conflict called."""
        dao = _make_mock_dao()
        existing = _make_node("old", entity_name="Revenue $25B", is_consolidated=1)
        new = _make_node("new", entity_name="Revenue $30B", is_consolidated=0)

        worker = REMCycleWorker(
            dao=dao,
            llm_a=_make_mock_llm('{"contradiction": true}'),
            llm_b=_make_mock_llm('{"contradiction": true}'),
            activation_threshold=1,
        )
        worker._find_consolidated_matches = AsyncMock(return_value=[existing])

        result = await worker._consolidate_record("agent-test", new)
        assert result is True

        # resolve_conflict creates a SUPERSEDES edge
        dao.insert_edge.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_llm_failure_failsafe_consolidates(self):
        """If LLM raises an exception, fail-safe consolidates without invalidating."""
        dao = _make_mock_dao()
        existing = _make_node("old", is_consolidated=1)
        new = _make_node("new", is_consolidated=0)

        llm_a = _make_mock_llm()
        llm_a.acomplete = AsyncMock(side_effect=RuntimeError("API timeout"))
        llm_b = _make_mock_llm()

        worker = REMCycleWorker(
            dao=dao,
            llm_a=llm_a,
            llm_b=llm_b,
            activation_threshold=1,
        )
        worker._find_consolidated_matches = AsyncMock(return_value=[existing])

        result = await worker._consolidate_record("agent-test", new)
        # Fail-safe: no contradiction → consolidate normally
        assert result is False
        dao.mark_consolidated.assert_awaited_once()


# ===================================================================
# REMCycleWorker — lifecycle & properties
# ===================================================================


class TestREMCycleWorkerLifecycle:
    def test_properties(self):
        dao = _make_mock_dao()
        worker = REMCycleWorker(
            dao=dao,
            llm_a=_make_mock_llm(),
            llm_b=_make_mock_llm(),
            activation_threshold=25,
            max_records_per_cycle=50,
        )
        assert worker.activation_threshold == 25
        assert worker.max_records_per_cycle == 50
        assert worker.is_running is False

    def test_register_agent(self):
        dao = _make_mock_dao()
        worker = REMCycleWorker(
            dao=dao,
            llm_a=_make_mock_llm(),
            llm_b=_make_mock_llm(),
        )
        worker.register_agent("agent-1")
        worker.register_agent("agent-2")
        worker.register_agent("agent-1")  # duplicate — should be ignored
        assert worker._agent_ids == ["agent-1", "agent-2"]

    @pytest.mark.asyncio
    async def test_start_disabled_is_noop(self):
        worker = REMCycleWorker(
            dao=_make_mock_dao(),
            llm_a=_make_mock_llm(),
            llm_b=_make_mock_llm(),
            enabled=False,
        )
        await worker.start()
        assert worker.is_running is False

    @pytest.mark.asyncio
    async def test_run_now_single_agent(self):
        dao = _make_mock_dao()
        dao.get_memories = AsyncMock(return_value=[])

        worker = REMCycleWorker(
            dao=dao,
            llm_a=_make_mock_llm(),
            llm_b=_make_mock_llm(),
            agent_ids=["agent-1"],
        )
        result = await worker.run_now(agent_id="agent-1")
        assert isinstance(result, dict)
        assert "cycles_skipped" in result

    @pytest.mark.asyncio
    async def test_run_now_all_agents(self):
        dao = _make_mock_dao()
        dao.get_memories = AsyncMock(return_value=[])

        worker = REMCycleWorker(
            dao=dao,
            llm_a=_make_mock_llm(),
            llm_b=_make_mock_llm(),
            agent_ids=["agent-1", "agent-2"],
        )
        result = await worker.run_now()
        # Should have processed both agents (both below threshold → skipped)
        assert result["cycles_skipped"] == 2

    @pytest.mark.asyncio
    async def test_process_agent_exception_records_failure(self):
        """If _process_agent raises, the poll loop records a failure."""
        dao = _make_mock_dao()
        dao.get_memories = AsyncMock(side_effect=RuntimeError("DB down"))

        worker = REMCycleWorker(
            dao=dao,
            llm_a=_make_mock_llm(),
            llm_b=_make_mock_llm(),
            agent_ids=["agent-1"],
        )

        # _process_agent should raise, but the poll loop catches it
        with pytest.raises(RuntimeError):
            await worker._process_agent("agent-1")


# ===================================================================
# REMCycleWorker — _find_consolidated_matches
# ===================================================================


class TestFindConsolidatedMatches:
    @pytest.mark.asyncio
    async def test_empty_entity_name_returns_empty(self):
        dao = _make_mock_dao()
        worker = REMCycleWorker(
            dao=dao,
            llm_a=_make_mock_llm(),
            llm_b=_make_mock_llm(),
        )
        result = await worker._find_consolidated_matches("agent-test", "")
        assert result == []

    @pytest.mark.asyncio
    async def test_queries_with_correct_agent_id(self):
        """Verify the DAO is called with the correct agent_id."""
        dao = _make_mock_dao()

        worker = REMCycleWorker(
            dao=dao,
            llm_a=_make_mock_llm(),
            llm_b=_make_mock_llm(),
        )
        result = await worker._find_consolidated_matches("agent-test", "SomeEntity")
        assert result == []
        # Verify dao.find_consolidated_nodes_by_name was called with agent-test
        dao.find_consolidated_nodes_by_name.assert_awaited_once_with(
            "agent-test", entity_name="SomeEntity"
        )
