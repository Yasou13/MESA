from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import aiosqlite
import numpy as np

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import config
from mesa_memory.observability.metrics import ObservabilityLayer
from mesa_memory.valence.drift import recalibrate_threshold
from mesa_memory.valence.novelty import calculate_novelty_score

logger = logging.getLogger("MESA_Valence")


@dataclass
class ValenceState:
    """Mutable novelty state that must never be shared between tenants."""

    ewmad_threshold: float
    records_since_recalibration: int = 0
    existing_embeddings: list = field(default_factory=list)
    memory_count: int = 0


def calculate_fitness_score(
    content: str, token_count: int, novelty_score: float = 1.0
) -> float:
    """Calculate the fitness score of a CMB candidate (0.0 - 1.0)."""
    word_count = len(content.split())

    word_density = word_count / token_count if token_count > 0 else 0.0
    density_norm = min(word_density / 1.0, 1.0)

    if 50 <= token_count <= 500:
        efficiency = 1.0
    elif token_count < 50:
        efficiency = max(0.1, token_count / 50.0)
    else:
        efficiency = max(0.1, 500.0 / token_count)

    score = (density_norm * 0.3) + (efficiency * 0.3) + (novelty_score * 0.4)
    return float(min(max(score, 0.0), 1.0))


class ValenceMotor:
    def __init__(  # type: ignore[no-untyped-def]
        self,
        llm_adapter: BaseUniversalLLMAdapter,
        obs_layer: ObservabilityLayer,
        storage=None,
    ):
        self.llm_adapter = llm_adapter
        self.obs_layer = obs_layer
        self.storage = storage
        self.bootstrap_threshold = config.bootstrap_cosine_threshold
        self._states: dict[str, ValenceState] = {}
        self._state_for("__unset__")

    def _state_for(self, agent_id: str) -> ValenceState:
        state = self._states.get(agent_id)
        if state is None:
            embeddings = self._hydrate_embeddings(agent_id)
            state = ValenceState(
                ewmad_threshold=self.bootstrap_threshold,
                existing_embeddings=embeddings,
                memory_count=len(embeddings),
            )
            self._states[agent_id] = state
        return state

    # Compatibility properties intentionally address only the unscoped
    # standalone/test caller. Production candidates always carry agent_id.
    @property
    def _ewmad_threshold(self) -> float:
        return self._state_for("__unset__").ewmad_threshold

    @_ewmad_threshold.setter
    def _ewmad_threshold(self, value: float) -> None:
        self._state_for("__unset__").ewmad_threshold = value

    @property
    def memory_count(self) -> int:
        return self._state_for("__unset__").memory_count

    @memory_count.setter
    def memory_count(self, value: int) -> None:
        self._state_for("__unset__").memory_count = value

    @property
    def existing_embeddings(self) -> list:
        return self._state_for("__unset__").existing_embeddings

    @existing_embeddings.setter
    def existing_embeddings(self, value: list) -> None:
        state = self._state_for("__unset__")
        state.existing_embeddings = value
        state.memory_count = len(value)

    @property
    def _records_since_recalibration(self) -> int:
        return self._state_for("__unset__").records_since_recalibration

    @_records_since_recalibration.setter
    def _records_since_recalibration(self, value: int) -> None:
        self._state_for("__unset__").records_since_recalibration = value

    async def save_state(self, db_path: str):  # type: ignore[no-untyped-def]
        """Persist the cognitive state (ewmad_threshold, memory_count) to SQLite."""
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS valence_state (key TEXT PRIMARY KEY, value TEXT)"
            )
            for agent_id, state in self._states.items():
                state_data = {
                    "ewmad_threshold": state.ewmad_threshold,
                    "memory_count": state.memory_count,
                }
                await db.execute(
                    "INSERT OR REPLACE INTO valence_state (key, value) VALUES (?, ?)",
                    (f"valence_core_state:{agent_id}", json.dumps(state_data)),
                )
            await db.commit()

    async def load_state(self, db_path: str):  # type: ignore[no-untyped-def]
        """Restore the cognitive state from SQLite if available."""
        logger.debug("VALENCE_STATE_LOAD_STARTED")
        try:
            async with aiosqlite.connect(db_path) as db:
                async with db.execute(
                    "SELECT key, value FROM valence_state WHERE key LIKE 'valence_core_state:%'"
                ) as cursor:
                    rows = await cursor.fetchall()
                for key, raw_state in rows:
                    agent_id = str(key).removeprefix("valence_core_state:")
                    if not agent_id:
                        continue
                    state_data = json.loads(raw_state)
                    state = self._state_for(agent_id)
                    state.ewmad_threshold = state_data.get(
                        "ewmad_threshold", self.bootstrap_threshold
                    )
                    state.memory_count = state_data.get(
                        "memory_count", state.memory_count
                    )
                    logger.info("Restored ValenceMotor state for agent_id=%s", agent_id)
        except Exception as e:
            logger.warning(
                f"Could not load ValenceMotor state (fresh setup or error): {e}"
            )

    def _hydrate_embeddings(self, agent_id: str) -> list:
        """Load existing embeddings from persistent vector storage.

        Falls back gracefully to an empty list when:
        - No storage was injected (unit-test / standalone mode).
        - The storage layer raises any exception (cold-start).

        Uses duck-typed interface: any storage backend exposing
        ``load_embedding_cache`` or ``get_all_embeddings`` is supported.
        """
        if self.storage is None:
            return []

        try:
            if hasattr(self.storage, "load_embedding_cache"):
                return self.storage.load_embedding_cache(  # type: ignore[Any]
                    agent_id=agent_id,
                    limit=config.max_embedding_history,
                )
            if hasattr(self.storage, "get_all_embeddings"):
                return self.storage.get_all_embeddings(  # type: ignore[Any]
                    agent_id=agent_id,
                    limit=config.max_embedding_history,
                )
        except Exception as exc:
            logger.warning(
                "Failed to hydrate embeddings from storage (cold-start): %s",
                exc,
            )
        return []

    def _get_current_threshold(self, state: ValenceState) -> float:
        n = state.memory_count
        interval = config.recalibration_interval
        if n < interval:
            return self.bootstrap_threshold
        if n > 3 * interval:
            return state.ewmad_threshold
        w = 1 / (
            1
            + np.exp(
                config.drift_sigmoid_weight * ((n - interval) / (2 * interval) - 0.5)
            )
        )
        return (1 - w) * self.bootstrap_threshold + w * state.ewmad_threshold  # type: ignore[no-any-return]

    def _recalibrate(self, state: ValenceState | None = None) -> None:
        if state is None:
            state = self._state_for("__unset__")
        state.ewmad_threshold = recalibrate_threshold(
            current_threshold=state.ewmad_threshold,
            existing_embeddings=state.existing_embeddings,
        )
        state.records_since_recalibration = 0
        self.obs_layer.metrics.set("valence_threshold", state.ewmad_threshold)

    async def evaluate(
        self, cmb_candidate: dict, current_state_signals: dict
    ) -> bool | str:
        _content = cmb_candidate.get("content_payload", "")
        _source = cmb_candidate.get("source", "")
        _performative = cmb_candidate.get("performative", "")
        _latency = cmb_candidate.get("resource_cost", {}).get("latency_ms", 0.0)
        cost = cmb_candidate.get("resource_cost", {})
        agent_id = cmb_candidate.get("agent_id", "__unset__")
        state = self._state_for(agent_id)

        if current_state_signals.get("error"):
            self.obs_layer.log_valence_decision(
                tier=1,
                decision="DISCARD",
                justification="ExecutionFailure signal detected",
                cost=cost,
            )
            return False

        if current_state_signals.get("format_violation"):
            self.obs_layer.log_valence_decision(
                tier=1,
                decision="DISCARD",
                justification="FormatViolation signal detected",
                cost=cost,
            )
            return False

        if current_state_signals.get("explicit_correction"):
            self.obs_layer.log_valence_decision(
                tier=1,
                decision="ADMIT",
                justification="ExplicitCorrection signal — force admit",
                cost=cost,
            )
            return True

        embedding = cmb_candidate.get("embedding", [])
        threshold = self._get_current_threshold(state)

        existing = (
            np.array(state.existing_embeddings)
            if state.existing_embeddings
            else np.array([])
        )
        new_emb = np.array(embedding)

        is_novel = await calculate_novelty_score(
            new_emb, existing, cosine_threshold=threshold
        )

        n_score = 1.0 if is_novel else 0.0

        # Fallback to rough token count if adapter doesn't provide it
        try:
            token_count = self.llm_adapter.get_token_count(_content)
        except Exception:
            token_count = len(_content.split())

        fitness = calculate_fitness_score(
            content=_content, token_count=token_count, novelty_score=n_score
        )

        if fitness < 0.3:
            self.obs_layer.log_valence_decision(
                tier=2,
                decision="DISCARD",
                justification=f"Low combined valence/fitness ({fitness:.4f})",
                cost=cost,
            )
            return False

        if is_novel:
            self.obs_layer.log_valence_decision(
                tier=2,
                decision="ADMIT",
                justification=f"Novelty detected (threshold={threshold:.4f}, fitness={fitness:.4f})",
                cost=cost,
            )
            self._admit(embedding, state)
            return True

        self.obs_layer.log_valence_decision(
            tier=2,
            decision="UNCERTAIN",
            justification=f"Novelty below threshold ({threshold:.4f}), fitness={fitness:.4f}, escalating to Tier 3",
            cost=cost,
        )

        # Defer Tier-3 cross-validation to the asynchronous consolidation loop
        cmb_candidate["tier3_deferred"] = True
        self.obs_layer.log_valence_decision(
            tier=3,
            decision="ADMIT",
            justification="Tier-3 validation deferred to background consolidation",
            cost=cost,
        )
        return "DEFERRED"

    def _admit(self, embedding: list, state: ValenceState):  # type: ignore[no-untyped-def]
        state.memory_count += 1
        state.records_since_recalibration += 1
        if embedding:
            state.existing_embeddings.append(embedding)
            if len(state.existing_embeddings) > config.max_embedding_history:
                state.existing_embeddings = state.existing_embeddings[
                    -config.max_embedding_history :
                ]
        if state.records_since_recalibration >= config.recalibration_interval:
            self._recalibrate(state)
