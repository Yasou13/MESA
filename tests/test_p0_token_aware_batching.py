import pytest
from unittest.mock import AsyncMock, MagicMock
from mesa_memory.consolidation.router import AdaptiveRouter
from mesa_memory.config import config

@pytest.mark.asyncio
async def test_selective_llm_judge_and_token_efficiency():
    """Verify that AdaptiveRouter skips redundant judge calls when small model response parses cleanly."""
    dao = MagicMock()
    dao.get_agent_routing_history = AsyncMock(return_value=[])

    small_llm = MagicMock()
    small_llm.acomplete = AsyncMock(return_value='{"decision": "STORE", "justification": "Valid event"}')

    validator = MagicMock()
    validator._parse_response.return_value = {"decision": "STORE", "justification": "Valid event"}

    config.legal_domain_mode = False

    router = AdaptiveRouter(
        dao=dao,
        small_llm=small_llm,
        dual_llm_validator=validator,
        audit_probability=0.0,
    )

    router._llm_judge_confidence = AsyncMock(return_value=0.95)

    record = {"content_payload": "Low risk payload", "performative": "INFORM"}
    result = await router.validate(record)

    # 1. Verification: Judge LLM call was SKIPPED because response parsed cleanly in low-risk mode!
    router._llm_judge_confidence.assert_not_called()
    assert result["route"] == "small_model"
    assert result["decision"] is True
