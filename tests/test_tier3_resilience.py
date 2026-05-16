"""
Tier-3 Validation Resilience Tests.

Verifies that infrastructure failures are surfaced as explicit
Tier3ValidationError exceptions and NEVER silently default to DISCARD.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from mesa_memory.consolidation.validator import Tier3ValidationError, Tier3Validator


def _make_record(cmb_id="test-cmb-001", tier3=True):
    return {
        "cmb_id": cmb_id,
        "content_payload": "Test content",
        "source": "test",
        "performative": "assert",
        "tier3_deferred": int(tier3),
    }


def _valid_json(decision="STORE"):
    return json.dumps({"decision": decision, "justification": "test"})


@pytest.fixture
def llm_a():
    m = MagicMock()
    m.complete = MagicMock(return_value=_valid_json("STORE"))
    return m


@pytest.fixture
def llm_b():
    m = MagicMock()
    m.complete = MagicMock(return_value=_valid_json("STORE"))
    return m


# --- Malformed JSON from LLMs ---


class TestMalformedJSON:
    @pytest.mark.asyncio
    async def test_llm_a_garbage(self, llm_a, llm_b):
        llm_a.complete.return_value = "not json {{{{"
        v = Tier3Validator(llm_a=llm_a, llm_b=llm_b)
        with pytest.raises(Tier3ValidationError, match="LLM_A"):
            await v.validate(_make_record())

    @pytest.mark.asyncio
    async def test_llm_b_garbage(self, llm_a, llm_b):
        llm_b.complete.return_value = "}{}{}{}"
        v = Tier3Validator(llm_a=llm_a, llm_b=llm_b)
        with pytest.raises(Tier3ValidationError, match="LLM_B"):
            await v.validate(_make_record())

    @pytest.mark.asyncio
    async def test_truncated_json(self, llm_a, llm_b):
        llm_a.complete.return_value = '{"decision": "STO'
        v = Tier3Validator(llm_a=llm_a, llm_b=llm_b)
        with pytest.raises(Tier3ValidationError):
            await v.validate(_make_record())

    @pytest.mark.asyncio
    async def test_markdown_wrapped_valid(self, llm_a, llm_b):
        llm_a.complete.return_value = f"```json\n{_valid_json('STORE')}\n```"
        v = Tier3Validator(llm_a=llm_a, llm_b=llm_b)
        assert await v.validate(_make_record()) is True


# --- Empty / non-string responses ---


class TestDegenerateResponses:
    @pytest.mark.asyncio
    async def test_empty_string(self, llm_a, llm_b):
        llm_a.complete.return_value = ""
        v = Tier3Validator(llm_a=llm_a, llm_b=llm_b)
        with pytest.raises(Tier3ValidationError, match="empty"):
            await v.validate(_make_record())

    @pytest.mark.asyncio
    async def test_none_response(self, llm_a, llm_b):
        llm_a.complete.return_value = None
        v = Tier3Validator(llm_a=llm_a, llm_b=llm_b)
        with pytest.raises(Tier3ValidationError):
            await v.validate(_make_record())

    @pytest.mark.asyncio
    async def test_numeric_response(self, llm_a, llm_b):
        llm_a.complete.return_value = 42
        v = Tier3Validator(llm_a=llm_a, llm_b=llm_b)
        with pytest.raises(Tier3ValidationError):
            await v.validate(_make_record())

    @pytest.mark.asyncio
    async def test_whitespace_only(self, llm_a, llm_b):
        llm_a.complete.return_value = "   \n\t\n   "
        v = Tier3Validator(llm_a=llm_a, llm_b=llm_b)
        with pytest.raises(Tier3ValidationError):
            await v.validate(_make_record())


# --- Invalid decision values ---


class TestInvalidDecisionValues:
    @pytest.mark.asyncio
    async def test_missing_decision_field(self, llm_a, llm_b):
        llm_a.complete.return_value = json.dumps({"justification": "no decision"})
        v = Tier3Validator(llm_a=llm_a, llm_b=llm_b)
        with pytest.raises(Tier3ValidationError, match="invalid decision"):
            await v.validate(_make_record())

    @pytest.mark.asyncio
    async def test_decision_maybe(self, llm_a, llm_b):
        llm_a.complete.return_value = json.dumps({"decision": "MAYBE"})
        v = Tier3Validator(llm_a=llm_a, llm_b=llm_b)
        with pytest.raises(Tier3ValidationError, match="invalid decision"):
            await v.validate(_make_record())

    @pytest.mark.asyncio
    async def test_decision_null(self, llm_a, llm_b):
        llm_a.complete.return_value = json.dumps({"decision": None})
        v = Tier3Validator(llm_a=llm_a, llm_b=llm_b)
        with pytest.raises(Tier3ValidationError, match="invalid decision"):
            await v.validate(_make_record())


# --- Infrastructure errors (HTTP 429/500) ---


class TestInfrastructureErrors:
    @pytest.mark.asyncio
    async def test_llm_a_rate_limited(self, llm_a, llm_b):
        llm_a.complete.side_effect = RuntimeError("HTTP 429: Too Many Requests")
        v = Tier3Validator(llm_a=llm_a, llm_b=llm_b)
        with pytest.raises(RuntimeError, match="429"):
            await v.validate(_make_record())

    @pytest.mark.asyncio
    async def test_llm_b_server_error(self, llm_a, llm_b):
        llm_b.complete.side_effect = RuntimeError("HTTP 500: Internal Server Error")
        v = Tier3Validator(llm_a=llm_a, llm_b=llm_b)
        with pytest.raises(RuntimeError, match="500"):
            await v.validate(_make_record())

    @pytest.mark.asyncio
    async def test_timeout(self, llm_a, llm_b):
        llm_a.complete.side_effect = TimeoutError("Connection timed out")
        v = Tier3Validator(llm_a=llm_a, llm_b=llm_b)
        with pytest.raises(TimeoutError):
            await v.validate(_make_record())


# --- Consensus logic ---


class TestConsensusLogic:
    @pytest.mark.asyncio
    async def test_both_store(self, llm_a, llm_b):
        v = Tier3Validator(llm_a=llm_a, llm_b=llm_b)
        assert await v.validate(_make_record()) is True

    @pytest.mark.asyncio
    async def test_both_discard(self, llm_a, llm_b):
        llm_a.complete.return_value = _valid_json("DISCARD")
        llm_b.complete.return_value = _valid_json("DISCARD")
        v = Tier3Validator(llm_a=llm_a, llm_b=llm_b)
        assert await v.validate(_make_record()) is False

    @pytest.mark.asyncio
    async def test_disagree_store_discard(self, llm_a, llm_b):
        llm_a.complete.return_value = _valid_json("STORE")
        llm_b.complete.return_value = _valid_json("DISCARD")
        v = Tier3Validator(llm_a=llm_a, llm_b=llm_b)
        assert await v.validate(_make_record()) is False

    @pytest.mark.asyncio
    async def test_disagree_discard_store(self, llm_a, llm_b):
        llm_a.complete.return_value = _valid_json("DISCARD")
        llm_b.complete.return_value = _valid_json("STORE")
        v = Tier3Validator(llm_a=llm_a, llm_b=llm_b)
        assert await v.validate(_make_record()) is False


# --- ConsolidationLoop dead-letter integration ---


class TestDeadLetterIntegration:
    @pytest.mark.asyncio
    async def test_tier3_error_dead_letters(self):
        """Tier3ValidationError → dead_letter_queue, NOT soft_delete."""
        from mesa_memory.consolidation.loop import ConsolidationLoop

        mock_storage = MagicMock()
        mock_storage.raw_log.fetch_unconsolidated = AsyncMock(return_value=[])
        mock_storage.soft_delete_all = AsyncMock()

        # LLM returns valid JSON but with an invalid decision value
        # → triggers Tier3ValidationError in _parse_decision
        llm_a = MagicMock()
        llm_a.complete = MagicMock(
            return_value=json.dumps({"decision": "INVALID_VALUE"})
        )
        llm_b = MagicMock()
        llm_b.complete = MagicMock(return_value=_valid_json("STORE"))

        loop = ConsolidationLoop(
            storage_facade=mock_storage,
            embedder=MagicMock(),
            llm_a=llm_a,
            llm_b=llm_b,
            obs_layer=MagicMock(),
        )
        loop.dead_letter_queue.clear()

        await loop.run_batch([_make_record(tier3=True)])

        assert len(loop.dead_letter_queue) == 1
        assert loop.dead_letter_queue[0]["cmb_id"] == "test-cmb-001"
        mock_storage.soft_delete_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_malformed_json_dead_letters(self):
        """Malformed JSON → dead-lettered, never silently DISCARDED."""
        from mesa_memory.consolidation.loop import ConsolidationLoop

        mock_storage = MagicMock()
        mock_storage.raw_log.fetch_unconsolidated = AsyncMock(return_value=[])
        mock_storage.soft_delete_all = AsyncMock()

        llm_a = MagicMock()
        llm_a.complete = MagicMock(return_value="NOT_JSON")
        llm_b = MagicMock()
        llm_b.complete = MagicMock(return_value=_valid_json("STORE"))

        loop = ConsolidationLoop(
            storage_facade=mock_storage,
            embedder=MagicMock(),
            llm_a=llm_a,
            llm_b=llm_b,
            obs_layer=MagicMock(),
        )
        loop.dead_letter_queue.clear()

        await loop.run_batch([_make_record(tier3=True)])

        assert len(loop.dead_letter_queue) == 1
        assert "error" in loop.dead_letter_queue[0]
        mock_storage.soft_delete_all.assert_not_called()
