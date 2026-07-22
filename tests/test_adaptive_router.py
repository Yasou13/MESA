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
    # The _parse_decision logic runs synchronously in router.validate()
    validator._parse_decision = MagicMock(return_value="STORE")
    # The actual Tier-3 Dual-LLM validation is async
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
    # Must bypass Tier-3 (validator.validate should NOT be awaited)
    validator.validate.assert_not_awaited()

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
    # MUST trigger Tier-3 Dual-LLM (validator.validate MUST be awaited exactly once)
    validator.validate.assert_awaited_once_with(record)

    # Must return the dual LLM's decision
    assert decision["route"] == "dual_llm"
    assert decision["decision"] is True  # Based on our mock
    assert decision["reason"] == "dual_llm_fallback"


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
