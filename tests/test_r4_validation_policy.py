import pytest
from unittest.mock import MagicMock, AsyncMock

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.consolidation.policy import (
    DeterministicOnlyValidationPolicy,
    SingleLLMValidationPolicy,
    DualLLMValidationPolicy,
    get_validation_policy,
)
from mesa_memory.consolidation.router import AdaptiveRouter
from mesa_memory.consolidation.validator import Tier3Validator, Tier3ValidationError


class MockAdapter(BaseUniversalLLMAdapter):
    def __init__(self, response: str = '{"decision": "STORE", "justification": "Valid item"}', model_name: str = "mock-model"):
        self.response = response
        self.model_name = model_name
        self.call_count = 0

    def complete(self, prompt: str) -> str:
        self.call_count += 1
        return self.response

    async def acomplete(self, prompt: str) -> str:
        self.call_count += 1
        return self.response

    def embed(self, text: str) -> list[float]:
        return [0.1] * 384

    async def aembed(self, text: str) -> list[float]:
        return [0.1] * 384

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 384 for _ in texts]

    async def aembed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 384 for _ in texts]

    def get_token_count(self, text: str) -> int:
        return len(text.split())


class ErrorAdapter(BaseUniversalLLMAdapter):
    def complete(self, prompt: str) -> str:
        raise RuntimeError("API Connection Refused")

    async def acomplete(self, prompt: str) -> str:
        raise RuntimeError("API Connection Refused")

    def embed(self, text: str) -> list[float]:
        return [0.1] * 384

    async def aembed(self, text: str) -> list[float]:
        return [0.1] * 384

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 384 for _ in texts]

    async def aembed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 384 for _ in texts]

    def get_token_count(self, text: str) -> int:
        return len(text.split())


@pytest.mark.asyncio
async def test_mode_0_deterministic_only():
    policy = DeterministicOnlyValidationPolicy()
    assert policy.mode == 0
    assert policy.validator_count == 0
    assert policy.llm_validation_enabled is False

    record = {"content_payload": "Test content", "agent_id": "test_agent"}
    audit = await policy.validate_with_audit(record)

    assert audit["accepted"] is True
    assert audit["reason"] == "skipped_by_policy"
    assert audit["route"] == "deterministic_only"
    assert audit["decisions"]["primary"] == "SKIPPED_BY_POLICY"
    assert audit["decisions"]["secondary"] == "NOT_RUN"


@pytest.mark.asyncio
async def test_mode_1_single_llm_store_and_discard():
    # 1. STORE decision
    adapter_store = MockAdapter(response='{"decision": "STORE", "justification": "High relevance fact"}', model_name="model-a")
    policy_1 = SingleLLMValidationPolicy(adapter_store)
    assert policy_1.mode == 1
    assert policy_1.validator_count == 1
    assert policy_1.llm_validation_enabled is True

    record = {"content_payload": "Python 3.13 released", "agent_id": "test_agent"}
    audit_store = await policy_1.validate_with_audit(record)

    assert audit_store["accepted"] is True
    assert audit_store["decisions"]["primary"] == "STORE"
    assert audit_store["decisions"]["secondary"] == "NOT_RUN"
    assert audit_store["models"]["primary"] == "model-a"
    assert adapter_store.call_count == 1

    # 2. DISCARD decision
    adapter_discard = MockAdapter(response='{"decision": "DISCARD", "justification": "Spam noise"}', model_name="model-a")
    policy_discard = SingleLLMValidationPolicy(adapter_discard)
    audit_discard = await policy_discard.validate_with_audit(record)

    assert audit_discard["accepted"] is False
    assert audit_discard["decisions"]["primary"] == "DISCARD"
    assert audit_discard["decisions"]["secondary"] == "NOT_RUN"
    assert adapter_discard.call_count == 1


@pytest.mark.asyncio
async def test_mode_1_error_handling():
    error_adapter = ErrorAdapter()
    policy = SingleLLMValidationPolicy(error_adapter)
    record = {"content_payload": "Some text"}

    with pytest.raises(Tier3ValidationError):
        await policy.validate_with_audit(record)


@pytest.mark.asyncio
async def test_mode_2_dual_consensus():
    adapter_a = MockAdapter(response='{"decision": "STORE", "justification": "Validator A approves"}', model_name="model-a")
    adapter_b = MockAdapter(response='{"decision": "STORE", "justification": "Validator B approves"}', model_name="model-b")

    validator = Tier3Validator(adapter_a, adapter_b)
    policy_2 = DualLLMValidationPolicy(validator)

    assert policy_2.mode == 2
    assert policy_2.validator_count == 2
    assert policy_2.llm_validation_enabled is True

    record = {"content_payload": "Important fact"}
    audit = await policy_2.validate_with_audit(record)

    assert audit["accepted"] is True
    assert audit["decisions"]["primary"] == "STORE"
    assert audit["decisions"]["secondary"] == "STORE"
    assert adapter_a.call_count == 1
    assert adapter_b.call_count == 1


@pytest.mark.asyncio
async def test_mode_2_disagreement_fails_closed():
    adapter_a = MockAdapter(response='{"decision": "STORE", "justification": "Validator A approves"}', model_name="model-a")
    adapter_b = MockAdapter(response='{"decision": "DISCARD", "justification": "Validator B rejects"}', model_name="model-b")

    validator = Tier3Validator(adapter_a, adapter_b)
    policy_2 = DualLLMValidationPolicy(validator)

    record = {"content_payload": "Contested fact"}
    audit = await policy_2.validate_with_audit(record)

    # Disagreement -> fails closed (accepted = False)
    assert audit["accepted"] is False
    assert audit["decisions"]["primary"] == "STORE"
    assert audit["decisions"]["secondary"] == "DISCARD"


@pytest.mark.asyncio
async def test_router_respects_mode_zero_under_all_guards():
    dao = MagicMock()
    small_llm = MockAdapter()
    policy_0 = DeterministicOnlyValidationPolicy()

    router = AdaptiveRouter(dao=dao, small_llm=small_llm, validation_policy=policy_0)

    # Standard record
    rec1 = {"content_payload": "Regular payload"}
    decision1 = await router.validate(rec1)
    assert decision1["route"] == "deterministic_only"
    assert decision1["decision"] is True
    assert small_llm.call_count == 0

    # Explicit correction record
    rec2 = {"content_payload": "Correction: the meeting is moved to Friday"}
    decision2 = await router.validate(rec2)
    assert decision2["route"] == "deterministic_only"
    assert decision2["decision"] is True
    assert small_llm.call_count == 0

    # Provenance dual review flag
    rec3 = {"content_payload": "Legal statute", "_mesa_force_dual_llm": True}
    decision3 = await router.validate(rec3)
    assert decision3["route"] == "deterministic_only"
    assert decision3["decision"] is True
    assert small_llm.call_count == 0


@pytest.mark.asyncio
async def test_router_respects_mode_1_under_all_guards():
    dao = MagicMock()
    adapter_a = MockAdapter(response='{"decision": "STORE", "justification": "A admits"}', model_name="validator-a")
    policy_1 = SingleLLMValidationPolicy(adapter_a)

    router = AdaptiveRouter(dao=dao, small_llm=adapter_a, validation_policy=policy_1)

    rec = {"content_payload": "Update: server IP changed"}
    decision = await router.validate(rec)
    assert decision["route"] == "single_model"
    assert decision["decision"] is True
    assert adapter_a.call_count == 1
