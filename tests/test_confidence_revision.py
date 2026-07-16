"""
Phase 1.2 Verification: Confidence Score Revision — LLM-as-a-Judge Tests.

Proves that:
  1. The ``pseudo_entropy`` placeholder is completely excised from the source.
  2. ``_llm_judge_confidence()`` handles all 4 parse layers:
     - Direct float parse ("0.85")
     - JSON extraction ({"score": 0.85})
     - Regex float extraction from prose ("The score is 0.85.")
     - Fallback to 0.0 on total failure
  3. Return values are clamped to [0.0, 1.0].
  4. LLM exceptions degrade gracefully to 0.0 (triggers Dual-LLM fallback).
  5. The T_route threshold now operates on judge-evaluated metrics.
  6. Full routing integration: high confidence → small_model, low → dual_llm.

asyncio_mode = strict → every async test requires explicit @pytest.mark.asyncio.
"""

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mesa_memory.consolidation.router import AdaptiveRouter
from mesa_memory.consolidation.validator import Tier3Validator
from mesa_storage.dao import MemoryDAO

# ===================================================================
# Helpers
# ===================================================================


def _make_mock_dao() -> MagicMock:
    """Build a mock MemoryDAO."""
    dao = MagicMock(spec=MemoryDAO)
    dao.get_recent_telemetry_stats = AsyncMock(return_value={"total_audits": 0})
    dao.insert_routing_telemetry = AsyncMock()
    return dao


def _make_mock_llm(acomplete_return: str = "0.85") -> MagicMock:
    """Build a mock LLM adapter with configurable acomplete return."""
    llm = MagicMock()
    llm.acomplete = AsyncMock(return_value=acomplete_return)
    llm.complete = MagicMock(
        return_value='{"decision": "STORE", "justification": "ok"}'
    )
    llm.get_token_count = MagicMock(return_value=50)
    return llm


def _make_router(
    small_llm: MagicMock | None = None,
    t_route: float = 0.90,
) -> tuple[AdaptiveRouter, MagicMock, MagicMock]:
    """Build an AdaptiveRouter with mocked dependencies.

    Returns (router, dao_mock, small_llm_mock).
    """
    dao = _make_mock_dao()
    llm = small_llm or _make_mock_llm()
    llm_a = MagicMock()
    llm_b = MagicMock()
    validator = Tier3Validator(llm_a=llm_a, llm_b=llm_b)
    router = AdaptiveRouter(
        dao=dao,
        small_llm=llm,
        dual_llm_validator=validator,
        t_route=t_route,
        audit_probability=0.0,  # Disable audit for deterministic tests
    )
    return router, dao, llm


# ===================================================================
# TEST 1: pseudo_entropy is completely excised
# ===================================================================


class TestPseudoEntropyDeleted:
    """Verify the mathematically invalid pseudo_entropy is fully removed."""

    def test_no_pseudo_entropy_in_source(self):
        src = inspect.getsource(AdaptiveRouter)
        assert (
            "pseudo_entropy" not in src
        ), "pseudo_entropy still present in AdaptiveRouter source"

    def test_no_modulo_100_formula(self):
        src = inspect.getsource(AdaptiveRouter)
        assert (
            "% 100" not in src
        ), "The invalid `len(raw_response) % 100` formula is still present"

    def test_no_todo_placeholder_comment(self):
        src = inspect.getsource(AdaptiveRouter)
        assert (
            "simulate confidence" not in src.lower()
        ), "Placeholder 'simulate confidence' comment still present"

    def test_llm_judge_method_exists(self):
        assert hasattr(
            AdaptiveRouter, "_llm_judge_confidence"
        ), "_llm_judge_confidence method not found on AdaptiveRouter"

    def test_judge_prompt_exists(self):
        assert hasattr(
            AdaptiveRouter, "_JUDGE_PROMPT"
        ), "_JUDGE_PROMPT not found on AdaptiveRouter"
        prompt = AdaptiveRouter._JUDGE_PROMPT
        assert "Logical consistency" in prompt
        assert "Factual grounding" in prompt
        assert "0.0" in prompt
        assert "1.0" in prompt


# ===================================================================
# TEST 2: _llm_judge_confidence parse cascade
# ===================================================================


class TestJudgeConfidenceParseCascade:
    """Verify all 4 parse layers of _llm_judge_confidence."""

    @pytest.mark.asyncio
    async def test_layer1_direct_float(self):
        """LLM returns a clean float string → parsed directly."""
        router, _, llm = _make_router()
        # _llm_judge_confidence calls acomplete once (the judge prompt)
        llm.acomplete = AsyncMock(return_value="0.92")
        score = await router._llm_judge_confidence("test query", "test response")
        assert score == pytest.approx(0.92)

    @pytest.mark.asyncio
    async def test_layer1_integer_zero(self):
        """LLM returns '0' → parsed as 0.0."""
        router, _, _ = _make_router()
        score = (
            await router._llm_judge_confidence.__wrapped__(router, "q", "r")
            if hasattr(router._llm_judge_confidence, "__wrapped__")
            else None
        )

        # Direct test via mock
        router2, _, llm2 = _make_router()
        llm2.acomplete = AsyncMock(return_value="0")
        score = await router2._llm_judge_confidence("q", "r")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_layer1_integer_one(self):
        """LLM returns '1' → parsed as 1.0."""
        router, _, llm = _make_router()
        llm.acomplete = AsyncMock(return_value="1")
        score = await router._llm_judge_confidence("q", "r")
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_layer1_clamp_above(self):
        """LLM returns 1.5 → clamped to 1.0."""
        router, _, llm = _make_router()
        llm.acomplete = AsyncMock(return_value="1.5")
        score = await router._llm_judge_confidence("q", "r")
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_layer1_clamp_below(self):
        """LLM returns -0.3 → clamped to 0.0."""
        router, _, llm = _make_router()
        llm.acomplete = AsyncMock(return_value="-0.3")
        score = await router._llm_judge_confidence("q", "r")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_layer2_json_score_key(self):
        """LLM returns JSON with 'score' key."""
        router, _, llm = _make_router()
        llm.acomplete = AsyncMock(return_value='{"score": 0.78}')
        score = await router._llm_judge_confidence("q", "r")
        assert score == pytest.approx(0.78)

    @pytest.mark.asyncio
    async def test_layer2_json_confidence_key(self):
        """LLM returns JSON with 'confidence' key."""
        router, _, llm = _make_router()
        llm.acomplete = AsyncMock(return_value='{"confidence": 0.91}')
        score = await router._llm_judge_confidence("q", "r")
        assert score == pytest.approx(0.91)

    @pytest.mark.asyncio
    async def test_layer2_json_value_key(self):
        """LLM returns JSON with 'value' key."""
        router, _, llm = _make_router()
        llm.acomplete = AsyncMock(return_value='{"value": 0.65}')
        score = await router._llm_judge_confidence("q", "r")
        assert score == pytest.approx(0.65)

    @pytest.mark.asyncio
    async def test_layer3_regex_extraction_from_prose(self):
        """LLM returns prose with an embedded float."""
        router, _, llm = _make_router()
        llm.acomplete = AsyncMock(
            return_value="The confidence score is 0.87 based on my analysis."
        )
        score = await router._llm_judge_confidence("q", "r")
        assert score == pytest.approx(0.87)

    @pytest.mark.asyncio
    async def test_layer3_regex_extracts_first_valid_float(self):
        """Multiple floats in prose — regex picks the first one."""
        router, _, llm = _make_router()
        llm.acomplete = AsyncMock(return_value="Score: 0.72. Alternative: 0.88.")
        score = await router._llm_judge_confidence("q", "r")
        assert score == pytest.approx(0.72)

    @pytest.mark.asyncio
    async def test_layer4_fallback_on_garbage(self):
        """LLM returns total garbage → fallback to 0.0."""
        router, _, llm = _make_router()
        llm.acomplete = AsyncMock(return_value="I cannot evaluate this response.")
        score = await router._llm_judge_confidence("q", "r")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_layer4_fallback_on_empty(self):
        """LLM returns empty string → fallback to 0.0."""
        router, _, llm = _make_router()
        llm.acomplete = AsyncMock(return_value="")
        score = await router._llm_judge_confidence("q", "r")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_exception_degrades_to_zero(self):
        """LLM adapter raises → graceful degradation to 0.0."""
        router, _, llm = _make_router()
        llm.acomplete = AsyncMock(side_effect=RuntimeError("API down"))
        score = await router._llm_judge_confidence("q", "r")
        assert score == 0.0


# ===================================================================
# TEST 3: Full routing integration
# ===================================================================


class TestRoutingIntegration:
    """End-to-end: verify T_route operates on judge-evaluated confidence."""

    @pytest.mark.asyncio
    async def test_high_confidence_routes_to_small_model(self):
        """When judge returns 0.95 and T_route=0.90, small model is accepted."""
        router, dao, llm = _make_router(t_route=0.90)

        # Call sequence: (1) small model response, (2) judge score
        call_count = {"n": 0}

        async def _mock_acomplete(prompt, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Small model STORE response
                return '{"decision": "STORE", "justification": "Valid memory"}'
            else:
                # Judge: high confidence
                return "0.95"

        llm.acomplete = AsyncMock(side_effect=_mock_acomplete)

        record = {
            "cmb_id": "test-001",
            "content_payload": "EU GDPR Article 5",
            "source": "agent",
            "performative": "inform",
            "agent_id": "test_agent",
        }

        result = await router.validate(record)

        assert result["route"] == "small_model"
        assert result["decision"] is True
        assert result["reason"] == "small_model_confident"

    @pytest.mark.asyncio
    async def test_low_confidence_routes_to_dual_llm(self):
        """When judge returns 0.40 and T_route=0.90, Dual-LLM is triggered."""
        router, dao, llm = _make_router(t_route=0.90)

        call_count = {"n": 0}

        async def _mock_acomplete(prompt, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return '{"decision": "STORE", "justification": "Uncertain"}'
            else:
                return "0.40"  # Below T_route

        llm.acomplete = AsyncMock(side_effect=_mock_acomplete)

        # Mock the Dual-LLM validator to return True
        router.validator.validate = AsyncMock(return_value=True)

        record = {
            "cmb_id": "test-002",
            "content_payload": "Some uncertain content",
            "source": "agent",
            "performative": "inform",
            "agent_id": "test_agent",
        }

        result = await router.validate(record)

        assert result["route"] == "dual_llm"
        assert result["reason"] == "dual_llm_fallback"
        router.validator.validate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_judge_failure_forces_dual_llm(self):
        """When judge returns garbage (0.0), record escalates to Dual-LLM."""
        router, dao, llm = _make_router(t_route=0.90)

        call_count = {"n": 0}

        async def _mock_acomplete(prompt, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return '{"decision": "STORE", "justification": "ok"}'
            else:
                return "Cannot evaluate"  # → 0.0

        llm.acomplete = AsyncMock(side_effect=_mock_acomplete)
        router.validator.validate = AsyncMock(return_value=True)

        record = {
            "cmb_id": "test-003",
            "content_payload": "Content",
            "source": "agent",
            "performative": "inform",
            "agent_id": "test_agent",
        }

        result = await router.validate(record)

        assert result["route"] == "dual_llm"
        assert result["reason"] == "dual_llm_fallback"

    @pytest.mark.asyncio
    async def test_schema_failure_overrides_confidence_to_zero(self):
        """When small model returns invalid JSON, confidence is forced to 0.0."""
        router, dao, llm = _make_router(t_route=0.90)

        call_count = {"n": 0}

        async def _mock_acomplete(prompt, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "NOT JSON AT ALL"  # Will fail schema parse
            else:
                return "0.99"  # High confidence — but irrelevant

        llm.acomplete = AsyncMock(side_effect=_mock_acomplete)
        router.validator.validate = AsyncMock(return_value=False)

        record = {
            "cmb_id": "test-004",
            "content_payload": "Garbage",
            "source": "agent",
            "performative": "inform",
            "agent_id": "test_agent",
        }

        result = await router.validate(record)

        # Schema failure → requires_fallback=True, confidence=0.0
        assert result["route"] == "dual_llm"

    @pytest.mark.asyncio
    async def test_legal_domain_mode_bypasses_judge(self):
        """In legal domain mode, judge is never called."""
        router, dao, llm = _make_router(t_route=0.90)

        with patch("mesa_memory.consolidation.router.config") as mock_config:
            mock_config.legal_domain_mode = True

            record = {
                "cmb_id": "test-005",
                "content_payload": "Legal text",
                "source": "agent",
                "performative": "inform",
            }

            result = await router.validate(record)

        assert result["route"] == "dual_llm"
        assert result["reason"] == "legal_domain_strict_mode"
        assert result["decision"] is None

        # LLM was NEVER called (judge was bypassed)
        llm.acomplete.assert_not_awaited()


# ===================================================================
# TEST 4: Judge prompt structure
# ===================================================================


class TestJudgePromptStructure:
    """Verify the judge prompt template is well-formed."""

    def test_prompt_contains_query_placeholder(self):
        assert "{query}" in AdaptiveRouter._JUDGE_PROMPT

    def test_prompt_contains_response_placeholder(self):
        assert "{response}" in AdaptiveRouter._JUDGE_PROMPT

    def test_prompt_specifies_float_range(self):
        prompt = AdaptiveRouter._JUDGE_PROMPT
        assert "0.0" in prompt
        assert "1.0" in prompt

    def test_prompt_specifies_no_explanation(self):
        prompt = AdaptiveRouter._JUDGE_PROMPT
        assert "NOTHING else" in prompt

    def test_prompt_evaluates_two_axes(self):
        prompt = AdaptiveRouter._JUDGE_PROMPT
        assert "Logical consistency" in prompt
        assert "Factual grounding" in prompt


# ===================================================================
# TEST 5: Return type contract
# ===================================================================


class TestReturnTypeContract:
    """Verify RoutingDecision shape is maintained across all paths."""

    @pytest.mark.asyncio
    async def test_small_model_path_returns_routing_decision(self):
        router, _, llm = _make_router(t_route=0.50)  # Low threshold

        call_count = {"n": 0}

        async def _mock(prompt, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return '{"decision": "STORE", "justification": "ok"}'
            return "0.95"

        llm.acomplete = AsyncMock(side_effect=_mock)

        record = {
            "content_payload": "x",
            "source": "s",
            "performative": "p",
            "agent_id": "a",
        }
        result = await router.validate(record)

        assert "route" in result
        assert "decision" in result
        assert "reason" in result
        assert isinstance(result["route"], str)
        assert isinstance(result["decision"], bool)
        assert isinstance(result["reason"], str)
