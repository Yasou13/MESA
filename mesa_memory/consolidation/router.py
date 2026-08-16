"""
Adaptive Routing Layer for MESA Consolidation Pipeline.

Implements Cost Optimization via Adaptive LLM Routing.
Defaults to a smaller, cheaper LLM for extraction validation, and falls back
to the expensive Dual-LLM only when uncertain.

Features:
- Temperature Scaling for Expected Calibration Error (ECE) minimization.
- 5% Audit Sampling for continuous telemetry and feedback loops.
- Dynamic Thresholding to adapt to model hallucination rates.
- Unified ``RoutingDecision`` return contract (B-5 fix).
"""

import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from typing import Any, TypedDict

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import config
from mesa_memory.consolidation.policy import (
    DeterministicOnlyValidationPolicy,
    DualLLMValidationPolicy,
    ValidationPolicy,
)
from mesa_memory.consolidation.validator import (
    Tier3Validator,
)
from mesa_memory.observability.metrics import ObservabilityLayer
from mesa_storage.dao import MemoryDAO

logger = logging.getLogger("MESA_Router")

_EXPLICIT_CORRECTION_RE = re.compile(
    r"(?i)\b(?:correction|corrected|update[ds]?|supersedes|replaces|"
    r"yanlış|düzelt(?:me|ildi)?|güncell(?:e|endi)|yerine)\b"
)


def _requires_tier3_correction_review(record: dict) -> bool:
    """Identify explicit update/correction language conservatively.

    This is a routing signal, never a truth decision: matching records are
    sent to the independent Tier-3 validators rather than accepted or
    discarded by novelty heuristics.
    """
    return bool(_EXPLICIT_CORRECTION_RE.search(str(record.get("content_payload", ""))))


# ---------------------------------------------------------------------------
# B-5 FIX: Canonical return type for all AdaptiveRouter.validate() paths
# ---------------------------------------------------------------------------


class RoutingDecision(TypedDict):
    """Unified return contract for ``AdaptiveRouter.validate()``.

    Every execution path — normal accept, legal-domain bypass, and
    dual-LLM fallback — MUST return this exact shape.  Downstream
    consumers (``ConsolidationLoop.run_batch``) rely on ``decision``
    to gate admission and ``route`` to detect forwarding intent.

    Fields:
        route:    Which model produced the decision.
                  One of ``"small_model"``, ``"dual_llm"``.
        decision: ``True`` (STORE), ``False`` (DISCARD), or ``None``
                  when the decision is deferred to a downstream gate
                  (e.g. legal-domain bypass routes to dual_llm without
                  evaluating here).
        reason:   Human-readable justification for observability.
    """

    route: str
    decision: bool | None
    reason: str
    tier3_audit: dict[str, Any] | None


@dataclass
class RoutingState:
    """Adaptive routing values owned by exactly one tenant."""

    threshold: float
    last_update_time: float = 0.0


class _BoundedRoutingStates:
    """Small locked LRU for adaptive agent state held by one worker."""

    def __init__(self, *, max_entries: int, ttl_seconds: float) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, tuple[RoutingState, float]] = OrderedDict()
        self._lock = RLock()

    def get_or_create(self, agent_id: str, threshold: float) -> RoutingState:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            existing = self._entries.get(agent_id)
            if existing is not None:
                self._entries.move_to_end(agent_id)
                return existing[0]
            state = RoutingState(threshold)
            self._entries[agent_id] = (state, now + self._ttl_seconds)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
            return state

    def __getitem__(self, agent_id: str) -> RoutingState:
        """Preserve read-only mapping access used by diagnostics and tests."""
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            state, _ = self._entries[agent_id]
            self._entries.move_to_end(agent_id)
            return state

    def __len__(self) -> int:
        """Expose the observable live size without leaking the backing map."""
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            return len(self._entries)

    def _prune(self, now: float) -> None:
        for key, (_, expires_at) in list(self._entries.items()):
            if expires_at <= now:
                del self._entries[key]


class AdaptiveRouter:
    """Routes validation requests between a small LLM and Dual-LLM gate.

    Confidence scoring uses the **LLM-as-a-Judge** pattern: the Tier-1
    response is evaluated by a lightweight judge prompt that returns a
    strict float in [0.0, 1.0] representing logical consistency and
    factual grounding.  This replaces the previous pseudo-entropy
    placeholder that was mathematically invalid.
    """

    # ------------------------------------------------------------------
    # LLM-as-a-Judge evaluator prompt
    # ------------------------------------------------------------------

    _JUDGE_PROMPT = """\
You are a strict quality evaluator for an AI memory system.

TASK: Evaluate how well the RESPONSE answers the QUERY.
Score on two axes:
  1. Logical consistency — is the response internally coherent?
  2. Factual grounding — does it make claims supported by the query context?

QUERY:
{query}

RESPONSE:
{response}

Return ONLY a single float between 0.0 and 1.0 (inclusive).
- 0.0 = completely incoherent or fabricated
- 0.5 = partially correct but uncertain
- 1.0 = fully consistent and well-grounded

Output the float and NOTHING else. No explanation, no JSON, no markdown."""

    def __init__(
        self,
        dao: MemoryDAO,
        small_llm: BaseUniversalLLMAdapter,
        dual_llm_validator: Tier3Validator | ValidationPolicy | None = None,
        *,
        validation_policy: ValidationPolicy | None = None,
        t_route: float = 0.85,
        audit_probability: float = 0.05,
        obs_layer: ObservabilityLayer | None = None,
    ):
        self.dao = dao
        self.small_llm = small_llm
        if validation_policy is not None:
            self.validation_policy = validation_policy
        elif isinstance(dual_llm_validator, ValidationPolicy):
            self.validation_policy = dual_llm_validator
        elif isinstance(dual_llm_validator, Tier3Validator):
            self.validation_policy = DualLLMValidationPolicy(dual_llm_validator)
        else:
            self.validation_policy = DeterministicOnlyValidationPolicy()

        self.validator = (
            self.validation_policy.validator
            if isinstance(self.validation_policy, DualLLMValidationPolicy)
            else self.validation_policy
        )
        # Retained as the configured default for compatibility.  Live routing
        # decisions use the agent-scoped state below.
        self.t_route = t_route
        self.audit_probability = audit_probability

        # Valence Motor — persists adaptive novelty thresholds (EWMAD).
        # Must be assigned here so server.py lifespan hooks can access
        # it via getattr(router, "valence_motor") for save/load_state.
        from mesa_memory.valence.core import ValenceMotor

        _obs = obs_layer or ObservabilityLayer()
        self.valence_motor = ValenceMotor(
            llm_adapter=self.small_llm,
            obs_layer=_obs,
            storage=self.dao,
        )

        # Dynamic threshold state is strictly tenant-scoped.  A noisy tenant
        # must never alter another tenant's routing cost/quality trade-off.
        # The router lives for the process lifetime.  Agent-scoped adaptive
        # state therefore needs a real capacity bound rather than a plain
        # dictionary that grows with every tenant ever seen by this worker.
        self._routing_states = _BoundedRoutingStates(
            max_entries=512,
            ttl_seconds=3600.0,
        )
        self._update_interval = 60.0  # seconds

    def _state_for(self, agent_id: str) -> RoutingState:
        return self._routing_states.get_or_create(agent_id, self.t_route)

    async def update_dynamic_threshold(self, agent_id: str):  # type: ignore[no-untyped-def]
        """Periodically recalibrate T_route based on recent audit performance."""
        now = time.time()
        state = self._state_for(agent_id)
        if (now - state.last_update_time) < self._update_interval:
            return

        state.last_update_time = now
        try:
            stats = await self.dao.get_recent_telemetry_stats(agent_id, limit=100)
            total_audits = stats.get("total_audits", 0)
            hallucinations = stats.get("hallucinations", 0)

            if total_audits > 0:
                error_rate = hallucinations / total_audits
                old_t = state.threshold

                if error_rate > 0.05:
                    # Mathematically penalize (demand higher confidence)
                    state.threshold = min(0.95, state.threshold + 0.05)
                    logger.warning(
                        "DYNAMIC_THRESHOLD | Error rate %.2f%% > 5%%. Increased T_route from %.2f to %.2f",
                        error_rate * 100,
                        old_t,
                        state.threshold,
                    )
                elif error_rate == 0.0:
                    # Safely decay (maximize cost savings)
                    state.threshold = max(0.60, state.threshold - 0.02)
                    logger.info(
                        "DYNAMIC_THRESHOLD | Error rate 0%%. Decreased T_route from %.2f to %.2f",
                        old_t,
                        state.threshold,
                    )
        except Exception as e:
            logger.error(
                "DYNAMIC_THRESHOLD | Failed to update dynamic threshold: %s", e
            )

    # ------------------------------------------------------------------
    # LLM-as-a-Judge confidence evaluation
    # ------------------------------------------------------------------

    async def _llm_judge_confidence(self, query: str, response: str) -> float:
        """Evaluate the Tier-1 response quality via LLM-as-a-Judge.

        Sends the original query and the small-model response to a
        lightweight evaluator prompt.  The judge returns a strict float
        in [0.0, 1.0] representing logical consistency and factual
        grounding.

        Parse cascade (4 layers):
            1. Direct ``float()`` on stripped output.
            2. JSON extraction (``{"score": 0.85}`` format).
            3. Regex float extraction from prose.
            4. Fallback to ``0.0`` (forces Dual-LLM escalation).

        Args:
            query: The original validation prompt sent to the small model.
            response: The raw string response from the small model.

        Returns:
            Float in [0.0, 1.0].  Clamped if the LLM returns out-of-range.
            Returns 0.0 on any failure (conservative — triggers fallback).
        """
        import json as _json
        import re

        try:
            judge_prompt = self._JUDGE_PROMPT.format(
                query=query[:1000],  # Truncate to prevent token overflow
                response=response[:500],
            )

            raw_score = await self.small_llm.acomplete(
                judge_prompt,
                max_tokens=16,
                temperature=0.0,
            )

            score_text = str(raw_score).strip()

            # Layer 1: Direct float parse
            try:
                score = float(score_text)
                return max(0.0, min(1.0, score))
            except ValueError:
                pass

            # Layer 2: JSON extraction (e.g., {"score": 0.85})
            try:
                parsed = _json.loads(score_text)
                if isinstance(parsed, dict):
                    for key in ("score", "confidence", "value"):
                        if key in parsed:
                            score = float(parsed[key])
                            return max(0.0, min(1.0, score))
            except (_json.JSONDecodeError, TypeError, ValueError):
                pass

            # Layer 3: Regex float extraction from prose
            float_match = re.search(r"\b(0(?:\.\d+)?|1(?:\.0+)?)\b", score_text)
            if float_match:
                score = float(float_match.group(1))
                return max(0.0, min(1.0, score))

            # Layer 4: Fallback — force Dual-LLM escalation
            logger.warning(
                "LLM_JUDGE_PARSE_FAILED | response_length=%d — defaulting to 0.0",
                len(score_text),
            )
            return 0.0

        except Exception as exc:
            logger.warning(
                "LLM_JUDGE_ERROR | exception_type=%s — defaulting to 0.0",
                type(exc).__name__,
            )
            return 0.0

    # ------------------------------------------------------------------
    # Main routing logic
    # ------------------------------------------------------------------

    async def validate(self, record: dict) -> RoutingDecision:
        """Adaptive validation logic obeying the configured ValidationPolicy.

        Mode 0:
            Unconditionally zero validation LLM calls. Returns SKIPPED_BY_POLICY.
        Mode 1:
            Single LLM validator participates. Returns single_model STORE/DISCARD.
        Mode 2:
            Dual LLM consensus gate (A+B). Both participate in final decision.
        """
        # Mode 0: Zero validation LLM unconditionally
        if self.validation_policy.mode == 0:
            audit = await self.validation_policy.validate_with_audit(record)
            return RoutingDecision(
                route="deterministic_only",
                decision=True,
                reason="skipped_by_policy",
                tier3_audit=audit,
            )

        # Mode 1: Single LLM validator unconditionally (no validator B)
        if self.validation_policy.mode == 1:
            audit = await self.validation_policy.validate_with_audit(record)
            accepted = bool(audit.get("accepted"))
            return RoutingDecision(
                route="single_model",
                decision=accepted,
                reason=str(
                    audit.get(
                        "reason",
                        "single_llm_store" if accepted else "single_llm_discard",
                    )
                ),
                tier3_audit=audit,
            )

        # Mode 2: True dual LLM consensus
        route_reason = "dual_llm_consensus"
        if _requires_tier3_correction_review(record):
            record["tier3_deferred"] = True
            record["explicit_correction"] = True
            route_reason = "explicit_correction_requires_tier3"
        elif getattr(config, "legal_domain_mode", False):
            route_reason = "legal_domain_strict_mode"
        elif record.get("_mesa_force_dual_llm"):
            route_reason = "provenance_dual_review"

        dual_llm_audit = await self.validation_policy.validate_with_audit(record)
        dual_llm_decision = bool(dual_llm_audit.get("accepted"))

        return RoutingDecision(
            route="dual_llm",
            decision=dual_llm_decision,
            reason=route_reason,
            tier3_audit={
                **dual_llm_audit,
                "route_reason": route_reason,
            },
        )
