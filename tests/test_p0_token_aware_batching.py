from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_memory.config import config
from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.consolidation.router import AdaptiveRouter


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


def test_extraction_batches_obey_configured_token_and_record_bounds(monkeypatch):
    monkeypatch.setattr(config, "max_batch_tokens", 40)
    monkeypatch.setattr(config, "consolidation_batch_size", 2)
    records = [
        {"content_payload": "a" * 32},
        {"content_payload": "b" * 32},
        {"content_payload": "c" * 32},
    ]

    batches = ConsolidationLoop._partition_extraction_batch(records)

    assert [len(batch) for batch in batches] == [1, 1, 1]
    assert all(len(batch) <= config.consolidation_batch_size for batch in batches)
    assert all(
        sum(ConsolidationLoop._estimate_record_tokens(record) for record in batch)
        <= config.max_batch_tokens
        for batch in batches
    )
