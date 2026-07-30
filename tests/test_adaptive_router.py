"""
Verification of Adaptive Routing Logic.

Tests the specific routing branches of the ``AdaptiveRouter``:
- Scenario A: High Confidence (0.95) bypasses Tier-3 Dual-LLM.
- Scenario B: Low Confidence (0.40) triggers Tier-3 Dual-LLM.

asyncio_mode = strict -> every async test requires explicit @pytest.mark.asyncio.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mesa_memory.consolidation.router import AdaptiveRouter
from mesa_memory.consolidation.validator import Tier3Validator
from mesa_storage.dao import MemoryDAO


def _make_router_and_mocks(t_route: float = 0.90):
    """Helper to build an AdaptiveRouter with all dependencies mocked."""
    dao = MagicMock(spec=MemoryDAO)
    dao.get_recent_telemetry_stats = AsyncMock(return_value={"total_audits": 0})
    dao.insert_routing_telemetry = AsyncMock()

    small_llm = MagicMock()
    # Ensure small_llm returns valid JSON for the schema check
    small_llm.acomplete = AsyncMock(
        return_value='{"decision": "STORE", "justification": "Clear memory"}'
    )

    validator = MagicMock(spec=Tier3Validator)
    # The response parser provides the safe primary-model receipt.
    validator._parse_response = MagicMock(
        return_value={"decision": "STORE", "justification": "Clear memory"}
    )
    validator._parse_decision = MagicMock(return_value="STORE")
    # The actual Tier-3 dual-LLM validation returns a durable audit receipt.
    validator.validate_with_audit = AsyncMock(
        return_value={
            "accepted": True,
            "route": "dual_llm",
            "decisions": {"primary": "STORE", "secondary": "STORE"},
            "justifications": {"primary": "grounded", "secondary": "grounded"},
            "models": {"primary": "test-a", "secondary": "test-b"},
            "prompt_version": "tier3-valence-v2",
            "reason": "dual_llm_consensus_store",
        }
    )
    validator.validate = AsyncMock(return_value=True)

    router = AdaptiveRouter(
        dao=dao,
        small_llm=small_llm,
        dual_llm_validator=validator,
        t_route=t_route,
        audit_probability=0.0,  # Disable random audits
    )

    return router, validator, small_llm


@pytest.mark.asyncio
async def test_scenario_a_high_confidence_bypasses_tier3():
    """Scenario A: High confidence bypasses Tier-3 validation."""
    router, validator, small_llm = _make_router_and_mocks()

    # 1. Mock the LLM-as-a-judge function to return 0.95 (High Confidence)
    router._llm_judge_confidence = AsyncMock(return_value=0.95)

    record = {
        "cmb_id": "test-scenario-a",
        "content_payload": "Some clear and concise fact.",
        "source": "user",
        "performative": "inform",
        "agent_id": "agent_1",
    }

    # 2. Execute validation
    with patch("mesa_memory.consolidation.router.config") as mock_config:
        mock_config.legal_domain_mode = False
        decision = await router.validate(record)

    # 3. Assertions
    # Must bypass Tier-3.
    validator.validate.assert_not_awaited()
    validator.validate_with_audit.assert_not_awaited()

    # Must return the small model's decision
    assert decision["route"] == "small_model"
    assert decision["decision"] is True
    assert decision["reason"] == "small_model_confident"


@pytest.mark.asyncio
async def test_scenario_b_low_confidence_triggers_tier3():
    """Scenario B: Low confidence triggers Tier-3 Dual-LLM validation."""
    router, validator, small_llm = _make_router_and_mocks()

    # 1. Mock the LLM-as-a-judge function to return 0.40 (Low Confidence)
    router._llm_judge_confidence = AsyncMock(return_value=0.40)

    record = {
        "cmb_id": "test-scenario-b",
        "content_payload": "Some ambiguous or contradictory text.",
        "source": "user",
        "performative": "inform",
        "agent_id": "agent_1",
    }

    # 2. Execute validation
    with patch("mesa_memory.consolidation.router.config") as mock_config:
        mock_config.legal_domain_mode = False
        decision = await router.validate(record)

    # 3. Assertions
    # MUST trigger Tier-3 Dual-LLM exactly once.
    validator.validate_with_audit.assert_awaited_once_with(record)

    # Must return the dual LLM's decision
    assert decision["route"] == "dual_llm"
    assert decision["decision"] is True  # Based on our mock
    assert decision["reason"] == "dual_llm_fallback"


@pytest.mark.asyncio
async def test_strong_provenance_forces_dual_review_before_small_model():
    router, validator, small_llm = _make_router_and_mocks()
    record = {
        "cmb_id": "source-backed-decision",
        "content_payload": "A source-backed architecture decision.",
        "_mesa_force_dual_llm": True,
    }

    with patch("mesa_memory.consolidation.router.config") as mock_config:
        mock_config.legal_domain_mode = False
        decision = await router.validate(record)

    assert decision == {
        "route": "dual_llm",
        "decision": None,
        "reason": "provenance_dual_review",
        "tier3_audit": None,
    }
    small_llm.acomplete.assert_not_awaited()
    validator.validate_with_audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_dynamic_threshold_and_cooldown_are_tenant_scoped():
    """One tenant's audit history must not affect another tenant's route gate."""
    router, _, _ = _make_router_and_mocks(t_route=0.85)

    async def telemetry(agent_id: str, *, limit: int):
        if agent_id == "agent-risky":
            return {"total_audits": 100, "hallucinations": 10}
        return {"total_audits": 100, "hallucinations": 0}

    router.dao.get_recent_telemetry_stats.side_effect = telemetry
    with patch("mesa_memory.consolidation.router.time.time", return_value=100.0):
        await router.update_dynamic_threshold("agent-risky")
        await router.update_dynamic_threshold("agent-clean")

    assert router._routing_states["agent-risky"].threshold == 0.90
    assert router._routing_states["agent-clean"].threshold == 0.83
    assert router._routing_states["agent-risky"].last_update_time == 100.0
    assert router._routing_states["agent-clean"].last_update_time == 100.0
from mesa_memory.consolidation.router import _requires_tier3_correction_review


def test_explicit_correction_is_routed_to_tier3() -> None:
    assert _requires_tier3_correction_review(
        {"content_payload": "Correction: the previous address is replaced."}
    )
