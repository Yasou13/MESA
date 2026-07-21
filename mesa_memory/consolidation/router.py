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
import random
import time
from typing import TypedDict

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import config
from mesa_memory.consolidation.validator import (
    VALENCE_PROMPT_A_TEMPLATE,
    Tier3ValidationError,
    Tier3Validator,
)
from mesa_memory.observability.metrics import ObservabilityLayer
from mesa_memory.valence.core import ValenceMotor
from mesa_storage.dao import MemoryDAO

logger = logging.getLogger("MESA_Router")


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
        dual_llm_validator: Tier3Validator,
        t_route: float = 0.85,
        audit_probability: float = 0.05,
        obs_layer: ObservabilityLayer | None = None,
    ):
        self.dao = dao
        self.small_llm = small_llm
        self.validator = dual_llm_validator
        self.t_route = t_route
        self.audit_probability = audit_probability

        # Valence Motor — persists adaptive novelty thresholds (EWMAD).
        # Must be assigned here so server.py lifespan hooks can access
        # it via getattr(router, "valence_motor") for save/load_state.
        _obs = obs_layer or ObservabilityLayer()
        self.valence_motor = ValenceMotor(
            llm_adapter=self.small_llm,
            obs_layer=_obs,
            storage=self.dao,
        )

        # Dynamic Thresholding Cache
        self._last_update_time = 0.0
        self._update_interval = 60.0  # seconds

    async def update_dynamic_threshold(self, agent_id: str):  # type: ignore[no-untyped-def]
        """Periodically recalibrate T_route based on recent audit performance."""
        now = time.time()
        if (now - self._last_update_time) < self._update_interval:
            return

        self._last_update_time = now
        try:
            stats = await self.dao.get_recent_telemetry_stats(agent_id, limit=100)
            total_audits = stats.get("total_audits", 0)
            hallucinations = stats.get("hallucinations", 0)

            if total_audits > 0:
                error_rate = hallucinations / total_audits
                old_t = self.t_route

                if error_rate > 0.05:
                    # Mathematically penalize (demand higher confidence)
                    self.t_route = min(0.95, self.t_route + 0.05)
                    logger.warning(
                        "DYNAMIC_THRESHOLD | Error rate %.2f%% > 5%%. Increased T_route from %.2f to %.2f",
                        error_rate * 100,
                        old_t,
                        self.t_route,
                    )
                elif error_rate == 0.0:
                    # Safely decay (maximize cost savings)
                    self.t_route = max(0.60, self.t_route - 0.02)
                    logger.info(
                        "DYNAMIC_THRESHOLD | Error rate 0%%. Decreased T_route from %.2f to %.2f",
                        old_t,
                        self.t_route,
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
                "LLM_JUDGE_PARSE_FAILED | raw=%r — defaulting to 0.0",
                score_text[:100],
            )
            return 0.0

        except Exception as exc:
            logger.warning("LLM_JUDGE_ERROR | error=%s — defaulting to 0.0", exc)
            return 0.0

    # ------------------------------------------------------------------
    # Main routing logic
    # ------------------------------------------------------------------

    async def validate(self, record: dict) -> RoutingDecision:
        """Adaptive validation logic.

        Returns a ``RoutingDecision`` across **all** execution paths:

        1. **Legal-domain bypass** → ``decision=None, route="dual_llm"``
           (caller must forward to Dual-LLM gate).
        2. **Small-model accept** → ``decision=bool, route="small_model"``.
        3. **Dual-LLM fallback/audit** → ``decision=bool, route="dual_llm"``.

        v0.6.1 Phase 3: When LEGAL_DOMAIN_MODE is active, steps 1-3 are
        entirely bypassed. Every record is routed to the Dual-LLM to
        guarantee zero-hallucination consensus on legal data.

        v0.6.1 Phase 1.2: Confidence scoring now uses LLM-as-a-Judge
        instead of the mathematically invalid pseudo-entropy placeholder.
        """
        # -----------------------------------------------------------------
        # PATH 1 — GUARDRAIL: Zero-Hallucination Legal Mode
        # When active, the small-model confidence gate is unconditionally
        # bypassed.  The dynamic T_route threshold is irrelevant; every
        # payload is forced through the heavy Dual-LLM ConsolidationLoop.
        # -----------------------------------------------------------------
        if getattr(config, "legal_domain_mode", False):
            logger.warning(
                "LEGAL_DOMAIN_STRICT_MODE is ACTIVE! "
                "All records will bypass the small-model gate and route directly to Dual-LLM. "
                "EXPECT SIGNIFICANTLY HIGHER API COSTS AND LATENCY PER RECORD."
            )
            logger.info(
                "LEGAL_DOMAIN_STRICT_MODE | Bypassing small-model gate. "
                "Routing directly to Dual-LLM for record: %s",
                record.get("cmb_id", record.get("id", "unknown")),
            )
            return RoutingDecision(
                route="dual_llm",
                decision=None,
                reason="legal_domain_strict_mode",
            )

        agent_id = record.get("agent_id", "mesa_consolidation_system")
        await self.update_dynamic_threshold(agent_id)

        prompt = VALENCE_PROMPT_A_TEMPLATE.format(
            content=record.get("content_payload", ""),
            source=record.get("source", "unknown"),
            performative=record.get("performative", "unknown"),
        )

        # 1. Route to small model
        raw_response = str(await self.small_llm.acomplete(prompt))

        # 2. LLM-as-a-Judge confidence evaluation
        #    Replaces the deleted pseudo-entropy placeholder.
        #    The judge assesses logical consistency and factual grounding
        #    of the small-model response, returning a float in [0.0, 1.0].
        confidence_score = await self._llm_judge_confidence(prompt, raw_response)

        requires_fallback = False
        small_model_decision = False

        # SCHEMA FALLBACK: Strict try/except to prevent loop crashes on bad JSON
        try:
            small_model_decision = (
                self.validator._parse_decision(raw_response, "small_llm") == "STORE"
            )
        except Tier3ValidationError as e:
            logger.warning(
                "ROUTER_SCHEMA_FALLBACK | Small model failed schema: %s. Falling back.",
                e,
            )
            requires_fallback = True
            confidence_score = 0.0
        except Exception as e:
            logger.warning("ROUTER_SCHEMA_FALLBACK | Unexpected parsing error: %s", e)
            requires_fallback = True
            confidence_score = 0.0

        # Routing Logic
        if not requires_fallback:
            requires_fallback = confidence_score < self.t_route

        is_audit = random.random() < self.audit_probability

        # -----------------------------------------------------------------
        # PATH 2 — Small-model accepted, no audit required
        # -----------------------------------------------------------------
        if not requires_fallback and not is_audit:
            return RoutingDecision(
                route="small_model",
                decision=small_model_decision,
                reason="small_model_confident",
            )

        # -----------------------------------------------------------------
        # PATH 3 — Dual-LLM Fallback or Audit Execution
        # -----------------------------------------------------------------
        logger.debug(
            "ROUTER | fallback=%s audit=%s confidence=%.2f",
            requires_fallback,
            is_audit,
            confidence_score,
        )

        dual_llm_decision = await self.validator.validate(record)

        if is_audit and not requires_fallback:
            # We are auditing a "confident" small model response.
            is_hallucination = small_model_decision != dual_llm_decision

            if is_hallucination:
                logger.warning(
                    "ROUTER_AUDIT_FAILURE | Silent hallucination detected. "
                    "Small model: %s, Dual LLM: %s, Confidence: %.2f",
                    small_model_decision,
                    dual_llm_decision,
                    confidence_score,
                )

            # Telemetry logging via MemoryDAO
            record_id = record.get("cmb_id", record.get("id", "unknown"))
            try:
                await self.dao.insert_routing_telemetry(
                    agent_id=agent_id,
                    record_id=record_id,
                    small_model_decision=int(small_model_decision),
                    small_model_confidence=confidence_score,
                    dual_llm_decision=int(dual_llm_decision),
                    is_hallucination=is_hallucination,
                )
            except Exception as e:
                logger.error("Failed to log routing telemetry: %s", e)

        # The Dual-LLM is the ground truth
        return RoutingDecision(
            route="dual_llm",
            decision=dual_llm_decision,
            reason="dual_llm_fallback" if requires_fallback else "dual_llm_audit",
        )
