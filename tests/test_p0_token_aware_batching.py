from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_memory.config import config
from mesa_memory.consolidation.policy import SingleLLMValidationPolicy
from mesa_memory.consolidation.router import AdaptiveRouter


@pytest.mark.asyncio
async def test_selective_llm_judge_and_token_efficiency(monkeypatch):
    """Verify that AdaptiveRouter skips redundant judge calls when small model response parses cleanly."""
    dao = MagicMock()
    dao.get_agent_routing_history = AsyncMock(return_value=[])

    small_llm = MagicMock()
    small_llm.acomplete = AsyncMock(
        return_value='{"decision": "STORE", "justification": "Valid event"}'
    )
    small_llm.complete = MagicMock(
        return_value='{"decision": "STORE", "justification": "Valid event"}'
    )

    monkeypatch.setattr(config, "legal_domain_mode", False)

    router = AdaptiveRouter(
        dao=dao,
        small_llm=small_llm,
        validation_policy=SingleLLMValidationPolicy(small_llm),
        audit_probability=0.0,
    )

    router._llm_judge_confidence = AsyncMock(return_value=0.95)

    record = {"content_payload": "Low risk payload", "performative": "INFORM"}
    result = await router.validate(record)

    # Mode 1 performs exactly one validation call and cannot add a judge call.
    router._llm_judge_confidence.assert_not_called()
    small_llm.complete.assert_called_once()
    small_llm.acomplete.assert_not_awaited()
    assert result["route"] == "single_model"
    assert result["decision"] is True
