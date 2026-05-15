import logging
import json
import aiosqlite

import numpy as np


from mesa_memory.config import config
from mesa_memory.observability.metrics import ObservabilityLayer
from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.valence.novelty import calculate_novelty_score
from mesa_memory.valence.drift import recalibrate_threshold

logger = logging.getLogger("MESA_Valence")


class ValenceMotor:
    def __init__(
        self,
        llm_adapter: BaseUniversalLLMAdapter,
        obs_layer: ObservabilityLayer,
        storage=None,
    ):
        self.llm_adapter = llm_adapter
        self.obs_layer = obs_layer
        self.storage = storage
        self.bootstrap_threshold = config.bootstrap_cosine_threshold
        self._ewmad_threshold = config.bootstrap_cosine_threshold
        self._records_since_recalibration = 0

        # --- Hydrate from persistent storage (survives process restarts) ---
        self.existing_embeddings = self._hydrate_embeddings()
        self.memory_count = len(self.existing_embeddings)
        if self.memory_count:
            logger.info(
                "ValenceMotor hydrated %d embeddings from persistent storage",
                self.memory_count,
            )

    async def save_state(self, db_path: str):
        """Persist the cognitive state (ewmad_threshold, memory_count) to SQLite."""
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS valence_state (key TEXT PRIMARY KEY, value TEXT)"
            )
            state_data = {
                "ewmad_threshold": self._ewmad_threshold,
                "memory_count": self.memory_count,
            }
            await db.execute(
                "INSERT OR REPLACE INTO valence_state (key, value) VALUES (?, ?)",
                ("valence_core_state", json.dumps(state_data)),
            )
            await db.commit()

    async def load_state(self, db_path: str):
        """Restore the cognitive state from SQLite if available."""
        try:
            async with aiosqlite.connect(db_path) as db:
                async with db.execute(
                    "SELECT value FROM valence_state WHERE key = 'valence_core_state'"
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        state_data = json.loads(row[0])
                        self._ewmad_threshold = state_data.get(
                            "ewmad_threshold", self.bootstrap_threshold
                        )
                        self.memory_count = state_data.get(
                            "memory_count", self.memory_count
                        )
                        logger.info(
                            f"Restored ValenceMotor state: threshold={self._ewmad_threshold:.4f}, count={self.memory_count}"
                        )
        except Exception as e:
            logger.warning(
                f"Could not load ValenceMotor state (fresh setup or error): {e}"
            )

    def _hydrate_embeddings(self) -> list:
        """Load existing embeddings from persistent vector storage.

        Falls back gracefully to an empty list when:
        - No storage was injected (unit-test / standalone mode).
        - The storage layer raises any exception (cold-start).

        Supports both ``StorageFacade`` (preferred, via ``load_embedding_cache``)
        and raw ``VectorStorage`` (via ``get_all_embeddings``) for flexibility.
        """
        if self.storage is None:
            return []

        try:
            # Prefer the StorageFacade convenience method
            if hasattr(self.storage, "load_embedding_cache"):
                return self.storage.load_embedding_cache(
                    limit=config.max_embedding_history,
                )
            # Fallback: direct VectorStorage access
            if hasattr(self.storage, "get_all_embeddings"):
                return self.storage.get_all_embeddings(
                    limit=config.max_embedding_history,
                )
        except Exception as exc:
            logger.warning(
                "Failed to hydrate embeddings from storage (cold-start): %s",
                exc,
            )
        return []

    def _get_current_threshold(self) -> float:
        n = self.memory_count
        interval = config.recalibration_interval
        if n < interval:
            return self.bootstrap_threshold
        if n > 3 * interval:
            return self._ewmad_threshold
        w = 1 / (
            1
            + np.exp(
                config.drift_sigmoid_weight * ((n - interval) / (2 * interval) - 0.5)
            )
        )
        return (1 - w) * self.bootstrap_threshold + w * self._ewmad_threshold

    def _recalibrate(self):
        self._ewmad_threshold = recalibrate_threshold(
            current_threshold=self._ewmad_threshold,
            existing_embeddings=self.existing_embeddings,
        )
        self._records_since_recalibration = 0
        self.obs_layer.metrics.set("valence_threshold", self._ewmad_threshold)

    async def evaluate(
        self, cmb_candidate: dict, current_state_signals: dict
    ) -> bool | str:
        _content = cmb_candidate.get("content_payload", "")
        _source = cmb_candidate.get("source", "")
        _performative = cmb_candidate.get("performative", "")
        _latency = cmb_candidate.get("resource_cost", {}).get("latency_ms", 0.0)
        cost = cmb_candidate.get("resource_cost", {})

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
        threshold = self._get_current_threshold()

        existing = (
            np.array(self.existing_embeddings)
            if self.existing_embeddings
            else np.array([])
        )
        new_emb = np.array(embedding)

        is_novel = await calculate_novelty_score(
            new_emb, existing, cosine_threshold=threshold
        )

        if is_novel:
            self.obs_layer.log_valence_decision(
                tier=2,
                decision="ADMIT",
                justification=f"Novelty detected (threshold={threshold:.4f})",
                cost=cost,
            )
            self._admit(embedding)
            return True

        self.obs_layer.log_valence_decision(
            tier=2,
            decision="UNCERTAIN",
            justification=f"Novelty below threshold ({threshold:.4f}), escalating to Tier 3",
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

    def _admit(self, embedding: list):
        self.memory_count += 1
        self._records_since_recalibration += 1
        if embedding:
            self.existing_embeddings.append(embedding)
            if len(self.existing_embeddings) > config.max_embedding_history:
                self.existing_embeddings = self.existing_embeddings[
                    -config.max_embedding_history :
                ]
        if self._records_since_recalibration >= config.recalibration_interval:
            self._recalibrate()
