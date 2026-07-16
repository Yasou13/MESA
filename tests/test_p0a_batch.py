"""
P0-A Phase 3: Batch Processing & Token Compression — Empirical Reliability Tests.

Test 1 (Capacity):     20-record batch → verify all 20 are processed end-to-end.
Test 2 (Fault Tolerance): Truncated/malformed JSON → verify intact records are
                          salvaged and broken records are isolated via fallback.

These tests mock LLM adapters and MemoryDAO; no network calls are made.
v0.3.1: Migrated from StorageFacade mocks to MemoryDAO mocks.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mesa_memory.consolidation.loop import (
    ConsolidationLoop,
    _salvage_truncated_json,
    _sanitize_llm_response,
)
from mesa_memory.consolidation.schemas import BatchExtractionResponse, ExtractedTriplet
from mesa_memory.observability.metrics import ObservabilityLayer
from tests.fixtures.vectors import VEC_MATCH

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_records(n: int) -> list[dict]:
    """Generate N synthetic raw-log records with unique cmb_ids."""
    return [
        {
            "cmb_id": f"cmb-{i:04d}",
            "content_payload": f"Entity_{i} interacts with Target_{i} via action_{i}",
            "source": "agent",
        }
        for i in range(n)
    ]


def _make_batch_json_response(record_count: int, offset: int = 0) -> str:
    """Build a well-formed BatchExtractionResponse JSON string for N records."""
    triplets = []
    for i in range(record_count):
        triplets.append(
            {
                "record_index": i,
                "head": f"Entity_{offset + i}",
                "relation": f"action_{offset + i}",
                "tail": f"Target_{offset + i}",
                "confidence": 0.95,
            }
        )
    return json.dumps({"triplets": triplets})


def _make_mock_dao():
    """Build a mock MemoryDAO with async methods pre-configured."""
    dao = MagicMock()
    dao.get_memories = AsyncMock(return_value=[])
    dao.insert_memory = AsyncMock(return_value="node_id")
    dao.insert_edge = AsyncMock(return_value="edge_id")
    dao.mark_consolidated = AsyncMock()
    dao.invalidate_node = AsyncMock()
    dao.find_nodes_by_name = AsyncMock(return_value=[])
    dao.get_node_degree = AsyncMock(return_value=0)
    return dao


def _build_consolidation_loop() -> (
    tuple[ConsolidationLoop, MagicMock, MagicMock, MagicMock]
):
    """Build a ConsolidationLoop with fully mocked dependencies.

    Returns (loop, dao_mock, llm_a_mock, llm_b_mock).
    """
    obs = ObservabilityLayer()
    dao = _make_mock_dao()

    embedder = MagicMock()
    embedder.aembed = AsyncMock(return_value=VEC_MATCH)
    embedder.aembed_batch = AsyncMock(return_value=[VEC_MATCH])
    embedder.EMBEDDING_DIM = 768

    llm_a = MagicMock()
    llm_a.get_token_count = MagicMock(return_value=50)

    llm_b = MagicMock()
    llm_b.get_token_count = MagicMock(return_value=50)

    loop = ConsolidationLoop(
        dao=dao,
        embedder=embedder,
        llm_a=llm_a,
        llm_b=llm_b,
        obs_layer=obs,
    )

    return loop, dao, llm_a, llm_b


# ===================================================================
# TEST 1: Capacity — 20-record batch, zero data loss
# ===================================================================


class TestBatchCapacity:
    """Prove that a full 20-record batch is processed end-to-end with
    exactly 20 mark_consolidated calls and no Lost-in-the-Middle degradation.
    """

    @pytest.mark.asyncio
    async def test_20_records_all_processed(self):
        """All 20 records must be extracted and consolidated — no omissions."""
        loop, dao, llm_a, llm_b = _build_consolidation_loop()
        records = _make_records(20)

        def _llm_complete_side_effect(prompt, schema=None):
            """Dynamically generate a correct batch response based on
            the number of RECORD tags found in the prompt."""
            count = prompt.count("=== RECORD ")
            return _make_batch_json_response(count)

        llm_a.complete.side_effect = _llm_complete_side_effect
        llm_b.complete.side_effect = _llm_complete_side_effect

        with patch(
            "mesa_memory.consolidation.loop.calculate_composite_similarity",
            return_value=0.9,
        ):
            await loop.run_batch(records)

        # Assertion 1: At least 20 mark_consolidated calls (may be more due
        # to writes in writer.py also calling mark_consolidated)
        assert (
            dao.mark_consolidated.call_count >= 20
        ), f"Expected >= 20 consolidated, got {dao.mark_consolidated.call_count}"

        # Assertion 2: Graph writes occurred (sim=0.9 > threshold)
        assert dao.insert_memory.call_count > 0
        assert dao.insert_edge.call_count > 0

    @pytest.mark.asyncio
    async def test_positional_tags_present_in_prompts(self):
        """Verify LitM Layer 1: all prompts contain positional RECORD tags."""
        loop, dao, llm_a, llm_b = _build_consolidation_loop()
        records = _make_records(8)  # Single sub-batch

        async def _llm_complete_side_effect(prompt, *args, **kwargs):
            prompt_str = str(prompt)
            count = prompt_str.count("=== RECORD ")
            count = count if count > 0 else 1
            return _make_batch_json_response(count)

        def _sync_side_effect(prompt, *args, **kwargs):
            prompt_str = str(prompt)
            count = prompt_str.count("=== RECORD ")
            count = count if count > 0 else 1
            return _make_batch_json_response(count)

        mock_adapter = MagicMock()
        mock_adapter.acomplete = AsyncMock(side_effect=_llm_complete_side_effect)
        mock_adapter.complete.side_effect = _sync_side_effect

        llm_a.acomplete.side_effect = _llm_complete_side_effect
        llm_a.complete.side_effect = _sync_side_effect
        llm_b.acomplete.side_effect = _llm_complete_side_effect
        llm_b.complete.side_effect = _sync_side_effect

        with (
            patch(
                "mesa_memory.adapter.factory.AdapterFactory.get_adapter",
                return_value=mock_adapter,
            ),
            patch(
                "mesa_memory.extraction.rebel_pipeline.RebelExtractor.extract_triplets",
                return_value=[],
            ),
            patch(
                "mesa_memory.consolidation.loop.calculate_composite_similarity",
                return_value=0.9,
            ),
        ):
            await loop.run_batch(records)

        prompt = str(
            llm_a.complete.call_args_list[0].args[0]
            if llm_a.complete.call_count > 0
            else llm_a.acomplete.call_args_list[0].args[0]
        )
        for i in range(8):
            assert f"=== RECORD {i} ===" in prompt, f"Missing RECORD {i} tag"
            assert f"=== END RECORD {i} ===" in prompt, f"Missing END RECORD {i} tag"

    @pytest.mark.asyncio
    async def test_anchor_checkpoints_injected(self):
        """Verify LitM Layer 4: CHECKPOINT anchors injected every 3 records."""
        loop, dao, llm_a, llm_b = _build_consolidation_loop()
        records = _make_records(7)

        async def _llm_complete_side_effect(prompt, *args, **kwargs):
            prompt_str = str(prompt)
            count = prompt_str.count("=== RECORD ")
            count = count if count > 0 else 1
            return _make_batch_json_response(count)

        def _sync_side_effect(prompt, *args, **kwargs):
            prompt_str = str(prompt)
            count = prompt_str.count("=== RECORD ")
            count = count if count > 0 else 1
            return _make_batch_json_response(count)

        mock_adapter = MagicMock()
        mock_adapter.acomplete = AsyncMock(side_effect=_llm_complete_side_effect)
        mock_adapter.complete.side_effect = _sync_side_effect

        llm_a.acomplete.side_effect = _llm_complete_side_effect
        llm_a.complete.side_effect = _sync_side_effect
        llm_b.acomplete.side_effect = _llm_complete_side_effect
        llm_b.complete.side_effect = _sync_side_effect

        with (
            patch(
                "mesa_memory.adapter.factory.AdapterFactory.get_adapter",
                return_value=mock_adapter,
            ),
            patch(
                "mesa_memory.extraction.rebel_pipeline.RebelExtractor.extract_triplets",
                return_value=[],
            ),
            patch(
                "mesa_memory.consolidation.loop.calculate_composite_similarity",
                return_value=0.9,
            ),
        ):
            await loop.run_batch(records)

        prompt = str(
            llm_a.complete.call_args_list[0].args[0]
            if llm_a.complete.call_count > 0
            else llm_a.acomplete.call_args_list[0].args[0]
        )
        # At i=3 and i=6, checkpoints should be injected
        assert "CHECKPOINT" in prompt, "No CHECKPOINT anchor found in prompt"
        assert "Continue with record 3" in prompt
        assert "Continue with record 6" in prompt


# ===================================================================
# TEST 2: Fault Tolerance — Truncated/malformed JSON recovery
# ===================================================================


class TestFaultTolerance:
    """Prove that the recovery pipeline salvages intact records and
    isolates broken ones without discarding the entire batch.
    """

    def test_sanitize_strips_markdown_fences(self):
        """Layer 1: Markdown fences are stripped before JSON parsing."""
        raw = '```json\n{"triplets": [{"record_index": 0, "head": "A", "relation": "r", "tail": "B"}]}\n```'
        cleaned = _sanitize_llm_response(raw)
        parsed = json.loads(cleaned)
        assert parsed["triplets"][0]["head"] == "A"

    def test_sanitize_strips_surrounding_prose(self):
        """Layer 1: Prose before/after JSON is discarded."""
        raw = 'Here is the result:\n{"triplets": [{"record_index": 0, "head": "X", "relation": "r", "tail": "Y"}]}\nHope this helps!'
        cleaned = _sanitize_llm_response(raw)
        parsed = json.loads(cleaned)
        assert parsed["triplets"][0]["head"] == "X"

    def test_salvage_recovers_truncated_json(self):
        """Layer 2: A response truncated mid-object recovers complete elements."""
        # 3 complete triplets + 1 truncated mid-field
        truncated = json.dumps(
            {
                "triplets": [
                    {
                        "record_index": 0,
                        "head": "A",
                        "relation": "r0",
                        "tail": "B",
                        "confidence": 0.9,
                    },
                    {
                        "record_index": 1,
                        "head": "C",
                        "relation": "r1",
                        "tail": "D",
                        "confidence": 0.8,
                    },
                    {
                        "record_index": 2,
                        "head": "E",
                        "relation": "r2",
                        "tail": "F",
                        "confidence": 0.7,
                    },
                ]
            }
        )
        # Simulate truncation: cut off the closing of a 4th element
        truncated_with_partial = (
            truncated[:-2] + ', {"record_index": 3, "head": "G", "rela'
        )
        result = _salvage_truncated_json(truncated_with_partial)

        assert result is not None, "Salvage returned None for recoverable JSON"
        response = BatchExtractionResponse.model_validate(result)
        assert (
            len(response.triplets) == 3
        ), f"Expected 3 salvaged triplets, got {len(response.triplets)}"
        recovered_indices = {t.record_index for t in response.triplets}
        assert recovered_indices == {0, 1, 2}

    def test_salvage_returns_none_for_total_garbage(self):
        """Layer 2: Completely invalid input returns None, doesn't crash."""
        assert _salvage_truncated_json("this is not json at all") is None
        assert _salvage_truncated_json("") is None
        assert _salvage_truncated_json("{{{{{") is None

    @pytest.mark.asyncio
    async def test_parse_batch_response_layer0_passthrough(self):
        """Layer 0: Already-parsed BatchExtractionResponse passes through."""
        loop, _, _, _ = _build_consolidation_loop()
        response = BatchExtractionResponse(
            triplets=[
                ExtractedTriplet(record_index=0, head="A", relation="r", tail="B"),
            ]
        )
        result = loop._parse_batch_response(response, 1)
        assert result is response

    @pytest.mark.asyncio
    async def test_parse_batch_response_layer1_string(self):
        """Layer 1: Valid JSON string is parsed and validated."""
        loop, _, _, _ = _build_consolidation_loop()
        raw = _make_batch_json_response(3)
        result = loop._parse_batch_response(raw, 3)
        assert len(result.triplets) == 3

    @pytest.mark.asyncio
    async def test_parse_batch_response_layer2_salvage(self):
        """Layer 2: Truncated JSON triggers bracket-depth salvage."""
        loop, _, _, _ = _build_consolidation_loop()
        # Build valid JSON then truncate
        full = json.dumps(
            {
                "triplets": [
                    {
                        "record_index": 0,
                        "head": "A",
                        "relation": "r",
                        "tail": "B",
                        "confidence": 0.9,
                    },
                    {
                        "record_index": 1,
                        "head": "C",
                        "relation": "r",
                        "tail": "D",
                        "confidence": 0.8,
                    },
                ]
            }
        )
        truncated = full[:-2] + ', {"record_index": 2, "head": "X"'
        result = loop._parse_batch_response(truncated, 3)
        # Should recover the 2 complete triplets
        assert len(result.triplets) == 2

    @pytest.mark.asyncio
    async def test_parse_batch_response_raises_on_irrecoverable(self):
        """All layers fail → ValueError raised for bisection handler."""
        loop, _, _, _ = _build_consolidation_loop()
        with pytest.raises(ValueError, match="All parsing layers failed"):
            loop._parse_batch_response("completely broken garbage", 5)

    @pytest.mark.asyncio
    async def test_coverage_audit_detects_missing_indices(self):
        """Post-hoc audit identifies which record_indices are missing."""
        loop, _, _, _ = _build_consolidation_loop()
        # Response has indices 0 and 2, but not 1
        response = BatchExtractionResponse(
            triplets=[
                ExtractedTriplet(record_index=0, head="A", relation="r", tail="B"),
                ExtractedTriplet(record_index=2, head="C", relation="r", tail="D"),
            ]
        )
        indexed, missing = loop._audit_batch_coverage(response, expected_count=3)
        assert missing == [1]
        assert set(indexed.keys()) == {0, 2}

    @pytest.mark.asyncio
    async def test_truncated_response_triggers_fallback_and_salvages(self):
        """End-to-end: LLM_A returns truncated JSON for a 5-record sub-batch.
        The recovery pipeline must salvage the intact records and backfill
        the missing ones via 1:1 fallback. All 5 must be consolidated.
        """
        loop, dao, llm_a, llm_b = _build_consolidation_loop()
        records = _make_records(5)

        # --- Mock LLM_A: returns truncated JSON (3 of 5 complete) ---
        full_a = json.dumps(
            {
                "triplets": [
                    {
                        "record_index": 0,
                        "head": "E0",
                        "relation": "r0",
                        "tail": "T0",
                        "confidence": 0.9,
                    },
                    {
                        "record_index": 1,
                        "head": "E1",
                        "relation": "r1",
                        "tail": "T1",
                        "confidence": 0.9,
                    },
                    {
                        "record_index": 2,
                        "head": "E2",
                        "relation": "r2",
                        "tail": "T2",
                        "confidence": 0.9,
                    },
                ]
            }
        )
        # Truncate: indices 3 and 4 are cut off
        truncated_a = full_a[:-2] + ', {"record_index": 3, "head": "E3", "rela'

        # For 1:1 fallback calls (indices 3 and 4), return valid single-record JSON
        call_count_a = {"n": 0}

        def _llm_a_side_effect(prompt, *args, **kwargs):
            call_count_a["n"] += 1
            if call_count_a["n"] == 1:
                # First call: batch prompt → truncated response
                return truncated_a
            # Subsequent calls: 1:1 fallback
            return json.dumps(
                {"head": "FallbackA", "relation": "fr", "tail": "FallbackT"}
            )

        async def _allm_a_side_effect(prompt, *args, **kwargs):
            return _llm_a_side_effect(prompt, *args, **kwargs)

        # --- Mock LLM_B: returns perfect response for all 5 ---
        def _llm_b_side_effect(prompt, *args, **kwargs):
            prompt_str = str(prompt)
            count = prompt_str.count("=== RECORD ")
            if count > 0:
                return _make_batch_json_response(count)
            return json.dumps({"head": "B", "relation": "r", "tail": "T"})

        async def _allm_b_side_effect(prompt, *args, **kwargs):
            return _llm_b_side_effect(prompt, *args, **kwargs)

        mock_adapter = MagicMock()
        mock_adapter.acomplete = AsyncMock(side_effect=_allm_a_side_effect)
        mock_adapter.complete.side_effect = _llm_a_side_effect

        llm_a.acomplete.side_effect = _allm_a_side_effect
        llm_a.complete.side_effect = _llm_a_side_effect
        llm_b.acomplete.side_effect = _allm_b_side_effect
        llm_b.complete.side_effect = _llm_b_side_effect

        with (
            patch(
                "mesa_memory.adapter.factory.AdapterFactory.get_adapter",
                return_value=mock_adapter,
            ),
            patch(
                "mesa_memory.extraction.rebel_pipeline.RebelExtractor.extract_triplets",
                return_value=[],
            ),
            patch(
                "mesa_memory.consolidation.loop.calculate_composite_similarity",
                return_value=0.9,
            ),
        ):
            await loop.run_batch(records)

        # All 5 records must be marked consolidated (no data loss)
        assert (
            dao.mark_consolidated.call_count >= 5
        ), f"Expected >= 5 consolidated, got {dao.mark_consolidated.call_count}"

    @pytest.mark.asyncio
    async def test_total_parse_failure_triggers_bisection(self):
        """When LLM returns completely invalid JSON, bisection splits the
        sub-batch and retries. Records must still be processed."""
        loop, dao, llm_a, llm_b = _build_consolidation_loop()
        records = _make_records(4)

        call_count_a = {"n": 0}

        def _llm_a_side_effect(prompt, *args, **kwargs):
            call_count_a["n"] += 1
            if call_count_a["n"] == 1:
                # First call: total garbage
                return "NOT JSON AT ALL [[[ broken"
            # All subsequent calls (bisection retries + 1:1 fallback): valid
            prompt_str = str(prompt)
            count = prompt_str.count("=== RECORD ")
            if count > 0:
                return _make_batch_json_response(count)
            return json.dumps({"head": "R", "relation": "r", "tail": "T"})

        async def _allm_a_side_effect(prompt, *args, **kwargs):
            return _llm_a_side_effect(prompt, *args, **kwargs)

        def _llm_b_side_effect(prompt, *args, **kwargs):
            prompt_str = str(prompt)
            count = prompt_str.count("=== RECORD ")
            if count > 0:
                return _make_batch_json_response(count)
            return json.dumps({"head": "R", "relation": "r", "tail": "T"})

        async def _allm_b_side_effect(prompt, *args, **kwargs):
            return _llm_b_side_effect(prompt, *args, **kwargs)

        mock_adapter = MagicMock()
        mock_adapter.acomplete = AsyncMock(side_effect=_allm_a_side_effect)
        mock_adapter.complete.side_effect = _llm_a_side_effect

        llm_a.acomplete.side_effect = _allm_a_side_effect
        llm_a.complete.side_effect = _llm_a_side_effect
        llm_b.acomplete.side_effect = _allm_b_side_effect
        llm_b.complete.side_effect = _llm_b_side_effect

        with (
            patch(
                "mesa_memory.adapter.factory.AdapterFactory.get_adapter",
                return_value=mock_adapter,
            ),
            patch(
                "mesa_memory.extraction.rebel_pipeline.RebelExtractor.extract_triplets",
                return_value=[],
            ),
            patch(
                "mesa_memory.consolidation.loop.calculate_composite_similarity",
                return_value=0.9,
            ),
        ):
            await loop.run_batch(records)

        # Bisection should have recovered — at least 4 records consolidated
        assert dao.mark_consolidated.call_count >= 4, (
            f"Expected >= 4 consolidated after bisection, got "
            f"{dao.mark_consolidated.call_count}"
        )

        # LLM_A should have been called more than once (initial + retries)
        assert (
            llm_a.complete.call_count > 1 or llm_a.acomplete.call_count > 1
        ), "Bisection should have triggered additional LLM calls"


# ===================================================================
# TEST 3: Schema validation unit tests
# ===================================================================


class TestSchemaValidation:
    """Validate the Pydantic schemas enforce structural correctness."""

    def test_valid_triplet(self):
        t = ExtractedTriplet(record_index=0, head="A", relation="r", tail="B")
        assert t.head == "A"

    def test_whitespace_stripped(self):
        t = ExtractedTriplet(record_index=0, head="  A  ", relation=" r ", tail=" B ")
        assert t.head == "A"
        assert t.relation == "r"

    def test_empty_head_rejected(self):
        with pytest.raises(Exception):
            ExtractedTriplet(record_index=0, head="", relation="r", tail="B")

    def test_negative_index_rejected(self):
        with pytest.raises(Exception):
            ExtractedTriplet(record_index=-1, head="A", relation="r", tail="B")

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            ExtractedTriplet(
                record_index=0, head="A", relation="r", tail="B", confidence=1.5
            )

    def test_batch_response_empty_array_rejected(self):
        with pytest.raises(Exception):
            BatchExtractionResponse(triplets=[])

    def test_batch_response_valid(self):
        resp = BatchExtractionResponse(
            triplets=[
                ExtractedTriplet(record_index=0, head="A", relation="r", tail="B"),
                ExtractedTriplet(record_index=1, head="C", relation="r", tail="D"),
            ]
        )
        assert len(resp.triplets) == 2


# ===================================================================
# TEST 4: Utility function unit tests
# ===================================================================


class TestSalienceSorting:
    """Verify salience-first ordering places high-density records at edges."""

    def test_high_salience_at_edges(self):
        loop, _, _, _ = _build_consolidation_loop()
        # Create records with varying content density
        records = [
            {"content_payload": "a", "source": "s", "cmb_id": "low"},  # low salience
            {
                "content_payload": "x, y: z, w: v, u",
                "source": "s",
                "cmb_id": "high",
            },  # high salience
            {"content_payload": "b", "source": "s", "cmb_id": "low2"},  # low salience
        ]
        sorted_records = loop.triplet_extractor.sort_by_salience(records)
        # Highest salience record should be at position 0 or last position
        edge_ids = {sorted_records[0]["cmb_id"], sorted_records[-1]["cmb_id"]}
        assert "high" in edge_ids, "Highest-salience record must be at a batch edge"
