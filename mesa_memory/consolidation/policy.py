"""
MESA Validation Policy Abstraction — Selectable Validation Assurance (Round 4).

Provides first-class policy implementations for:
- Mode 0: Deterministic validation only (zero LLM validator calls)
- Mode 1: Single LLM validator (one model STORE/DISCARD decision)
- Mode 2: Dual LLM validators with consensus (A+B independent agreement)
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.consolidation.validator import (
    TIER3_PROMPT_VERSION,
    VALENCE_PROMPT_A_TEMPLATE,
    Tier3ValidationError,
    Tier3Validator,
    _parse_validator_response,
    _safe_justification,
    _safe_model_name,
    tier3_provenance_context,
)


class ValidationPolicy(ABC):
    """Abstract interface for admission validation policies."""

    mode: int
    name: str
    llm_validation_enabled: bool
    validator_count: int

    @abstractmethod
    async def validate_with_audit(self, record: dict[str, Any]) -> dict[str, Any]:
        """Evaluate record and return a safe, bounded audit receipt."""
        ...

    async def validate(self, record: dict[str, Any]) -> bool:
        """Evaluate record and return boolean admission decision."""
        audit = await self.validate_with_audit(record)
        return bool(audit.get("accepted"))


class DeterministicOnlyValidationPolicy(ValidationPolicy):
    """Mode 0: Deterministic validation only. Zero LLM validator calls."""

    mode = 0
    name = "deterministic_only"
    llm_validation_enabled = False
    validator_count = 0

    async def validate_with_audit(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "route": "deterministic_only",
            "route_reason": "mode_zero_deterministic_only",
            "decisions": {"primary": "SKIPPED_BY_POLICY", "secondary": "NOT_RUN"},
            "justifications": {
                "primary": "Deterministic validation only by policy; LLM validation skipped.",
                "secondary": None,
            },
            "models": {"primary": "none", "secondary": None},
            "prompt_version": "mode-0-deterministic",
            "accepted": True,
            "reason": "skipped_by_policy",
        }


class SingleLLMValidationPolicy(ValidationPolicy):
    """Mode 1: Exactly one LLM validator participates in validation."""

    mode = 1
    name = "single_llm"
    llm_validation_enabled = True
    validator_count = 1

    def __init__(self, llm_a: BaseUniversalLLMAdapter) -> None:
        if llm_a is None:
            raise ValueError("SingleLLMValidationPolicy requires validator llm_a")
        self.llm_a = llm_a

    async def validate_with_audit(self, record: dict[str, Any]) -> dict[str, Any]:
        content = record.get("content_payload", "")
        source = tier3_provenance_context(record, default_source="XXXX")
        performative = record.get("performative", "")
        prompt = VALENCE_PROMPT_A_TEMPLATE.format(
            content=content,
            source=source,
            performative=performative,
        )
        loop = asyncio.get_running_loop()
        try:
            raw_a = await loop.run_in_executor(None, self.llm_a.complete, prompt)
        except Exception as exc:
            raise Tier3ValidationError(f"Validator LLM_A failed: {exc}") from exc

        response_a = _parse_validator_response(raw_a, "LLM_A")
        decision = response_a["decision"]
        accepted = decision == "STORE"
        reason = "single_llm_store" if accepted else "single_llm_discard"

        return {
            "route": "single_model",
            "route_reason": "single_llm_validation",
            "decisions": {"primary": decision, "secondary": "NOT_RUN"},
            "justifications": {
                "primary": response_a["justification"],
                "secondary": None,
            },
            "models": {
                "primary": _safe_model_name(self.llm_a),
                "secondary": None,
            },
            "prompt_version": TIER3_PROMPT_VERSION,
            "accepted": accepted,
            "reason": reason,
        }


class DualLLMValidationPolicy(ValidationPolicy):
    """Mode 2: Two independent LLM validators with consensus."""

    mode = 2
    name = "dual_llm"
    llm_validation_enabled = True
    validator_count = 2

    def __init__(self, validator: Tier3Validator) -> None:
        if validator is None:
            raise ValueError("DualLLMValidationPolicy requires a Tier3Validator")
        self.validator = validator

    async def validate_with_audit(self, record: dict[str, Any]) -> dict[str, Any]:
        return await self.validator.validate_with_audit(record)


def get_validation_policy(
    mode: int,
    llm_a: BaseUniversalLLMAdapter | None = None,
    llm_b: BaseUniversalLLMAdapter | None = None,
) -> ValidationPolicy:
    """Factory function to build the appropriate ValidationPolicy for a given mode."""
    if mode == 0:
        return DeterministicOnlyValidationPolicy()
    elif mode == 1:
        if llm_a is None:
            raise ValueError("Mode 1 validation requires validator llm_a")
        return SingleLLMValidationPolicy(llm_a)
    elif mode == 2:
        if llm_a is None or llm_b is None:
            raise ValueError("Mode 2 validation requires both validator llm_a and llm_b")
        return DualLLMValidationPolicy(Tier3Validator(llm_a, llm_b))
    else:
        raise ValueError(f"Invalid validation mode: {mode}")
