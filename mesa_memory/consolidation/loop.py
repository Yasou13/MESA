"""
Consolidation Loop — Batch orchestrator for the MESA knowledge pipeline.

Refactored into focused modules following the Single Responsibility Principle:

- ``parser.py``: Prompt templates, JSON sanitization/salvage, response parsing.
- ``extraction/triplet_extractor.py``: REBEL pipeline, LLM fallback, bisection.
- ``validator.py``: Tier-3 LLM consensus gate (``Tier3Validator``).
- ``writer.py``: Graph cross-validation and commit (``GraphWriter``).
- ``loop.py`` (this file): Pure orchestrator — batch lifecycle only.
"""

import asyncio
import json
import logging
import os
import time
from typing import Optional

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import config
from mesa_memory.consolidation.lock import calculate_composite_similarity
from mesa_memory.consolidation.parser import (  # noqa: F401
    BATCH_PROMPT_A_TEMPLATE,
    BATCH_PROMPT_B_TEMPLATE,
    PROMPT_A_TEMPLATE,
    PROMPT_B_TEMPLATE,
    BatchResponseParser,
    _estimate_salience,
    _salvage_truncated_json,
    _sanitize_llm_response,
)
from mesa_memory.consolidation.validator import Tier3ValidationError, Tier3Validator
from mesa_memory.consolidation.writer import GraphWriter
from mesa_memory.extraction.triplet_extractor import TripletExtractor
from mesa_memory.observability.metrics import ObservabilityLayer
from mesa_memory.storage import StorageFacade

logger = logging.getLogger("MESA_Consolidation")


# ---------------------------------------------------------------------------
# Persistent Queue
# ---------------------------------------------------------------------------
class PersistentQueue:
    def __init__(self, filepath: str):
        self.filepath = filepath
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)

    def append(self, item: dict):
        with open(self.filepath, "a") as f:
            f.write(json.dumps(item) + "\n")

    def clear(self):
        with open(self.filepath, "w") as f:  # noqa: F841
            pass

    def __len__(self):
        try:
            with open(self.filepath, "r") as f:
                return sum(1 for line in f if line.strip())
        except FileNotFoundError:
            return 0

    def __getitem__(self, index):
        try:
            with open(self.filepath, "r") as f:
                lines = [json.loads(line) for line in f if line.strip()]
            return lines[index]
        except FileNotFoundError:
            raise IndexError("Queue is empty")


# ---------------------------------------------------------------------------
# ConsolidationLoop — Pure Orchestrator
# ---------------------------------------------------------------------------


class ConsolidationLoop:
    """Orchestrates batch processing of raw log records through the
    consolidation pipeline.

    Delegates to:
    - ``TripletExtractor``: REBEL + LLM extraction with bisection retry.
    - ``Tier3Validator``: LLM consensus gate for deferred records.
    - ``GraphWriter``: Cross-validation scoring and graph commits.
    - ``BatchResponseParser``: Response parsing and recovery.

    Retains ownership of:
    - Batch queue management and lifecycle (start/stop).
    - Tier-3 validation gating.
    - Observability logging.
    """

    def __init__(
        self,
        storage_facade: StorageFacade,
        embedder: BaseUniversalLLMAdapter,
        llm_a: BaseUniversalLLMAdapter,
        llm_b: BaseUniversalLLMAdapter,
        obs_layer: ObservabilityLayer,
    ):
        self.storage = storage_facade
        self.embedder = embedder
        self.llm_a = llm_a
        self.llm_b = llm_b
        self.obs_layer = obs_layer
        self._running = False
        self.human_review_queue = PersistentQueue("./storage/human_review_queue.jsonl")
        self.dead_letter_queue = PersistentQueue("./storage/dead_letter_queue.jsonl")

        # Delegate modules
        self.triplet_extractor = TripletExtractor(llm_a=llm_a, llm_b=llm_b)
        self.validator = Tier3Validator(llm_a=llm_a, llm_b=llm_b)
        self.graph_writer = GraphWriter(
            storage_facade=storage_facade,
            embedder=embedder,
            human_review_queue=self.human_review_queue,
            similarity_fn=calculate_composite_similarity,
        )

    # Expose rebel_extractor for backward compatibility
    @property
    def rebel_extractor(self):
        return self.triplet_extractor.rebel_extractor

    async def start(self):
        self._running = True
        while self._running:
            records = await self.storage.raw_log.fetch_unconsolidated(
                limit=config.consolidation_batch_size,
            )
            if records:
                await self.run_batch(records)
            await asyncio.sleep(config.consolidation_idle_timeout)

    async def stop(self):
        self._running = False

    # -------------------------------------------------------------------
    # Backward-compatible delegation wrappers
    # -------------------------------------------------------------------

    def _sort_by_salience(self, records: list[dict]) -> list[dict]:
        """Delegate to TripletExtractor.sort_by_salience."""
        return self.triplet_extractor.sort_by_salience(records)

    @staticmethod
    def _build_records_block(sub_batch: list[dict]) -> str:
        """Delegate to TripletExtractor.build_records_block."""
        return TripletExtractor.build_records_block(sub_batch)

    def _parse_batch_response(
        self,
        raw_response,
        sub_batch_size: int,
    ):
        """Delegate to BatchResponseParser.parse."""
        return BatchResponseParser.parse(raw_response, sub_batch_size)

    def _audit_batch_coverage(self, response, expected_count: int):
        """Delegate to BatchResponseParser.audit_coverage."""
        return BatchResponseParser.audit_coverage(response, expected_count)

    async def _single_record_extract(self, record, llm, template):
        """Delegate to TripletExtractor._single_record_extract."""
        return await self.triplet_extractor._single_record_extract(
            record, llm, template
        )

    async def _retry_with_bisection(
        self, sub_batch, batch_template, fallback_template, llm, depth=0
    ):
        """Delegate to TripletExtractor._retry_with_bisection."""
        return await self.triplet_extractor._retry_with_bisection(
            sub_batch, batch_template, fallback_template, llm, depth
        )

    # -------------------------------------------------------------------
    # Core batch orchestrator
    # -------------------------------------------------------------------

    async def run_batch(self, batch: Optional[list[dict]] = None):
        """Process a batch of raw log records through the consolidation pipeline.

        P0-A compliant flow:
        1. Tier-3 validation via ``Tier3Validator``.
        2. Sort by salience (high-density records at edges, LitM Layer 2).
        3. Full extraction via ``TripletExtractor`` (REBEL → LLM → bisection).
        4. Cross-validate and commit via ``GraphWriter``.
        """
        if batch is None:
            batch = await self.storage.raw_log.fetch_unconsolidated(
                limit=config.consolidation_batch_size,
            )

        if not batch:
            return

        # --- Phase 1: Tier-3 validation gate ---
        ready_batch = []
        for record in batch:
            if record.get("tier3_deferred"):
                try:
                    is_valid = await self.validator.validate(record)
                except Tier3ValidationError as exc:
                    # Infrastructure error — do NOT treat as cognitive DISCARD
                    logger.error(
                        "Tier-3 validation error for %s (will retry): %s",
                        record.get("cmb_id", "?"),
                        exc,
                    )
                    self.dead_letter_queue.append(
                        {
                            "cmb_id": record.get("cmb_id", ""),
                            "error": str(exc),
                        }
                    )
                    continue

                if is_valid:
                    self.obs_layer.log_valence_decision(
                        tier=3,
                        decision="ADMIT",
                        justification="Deferred Tier-3 validation passed in consolidation loop",
                        cost={"token_count": 0, "latency_ms": 0.0},
                    )
                    ready_batch.append(record)
                else:
                    self.obs_layer.log_valence_decision(
                        tier=3,
                        decision="DISCARD",
                        justification="Deferred Tier-3 validation failed in consolidation loop",
                        cost={"token_count": 0, "latency_ms": 0.0},
                    )
                    await self.storage.soft_delete_all(record.get("cmb_id", ""))
            else:
                ready_batch.append(record)

        batch = ready_batch
        if not batch:
            return

        start_ms = time.time() * 1000
        batch_id = f"batch_{int(start_ms)}"

        # --- Phase 2: Salience-first ordering (LitM Layer 2) ---
        sorted_batch = self.triplet_extractor.sort_by_salience(batch)

        # --- Phase 3: Full extraction pipeline ---
        indexed_a, indexed_b = await self.triplet_extractor.extract_batch(sorted_batch)

        # --- Phase 4: Pre-fetch embeddings & commit via GraphWriter ---
        embedding_cache = await self.graph_writer.prefetch_embeddings(
            sorted_batch,
            indexed_a,
            indexed_b,
        )

        successful_writes, divergence_count = await self.graph_writer.commit_batch(
            sorted_batch,
            indexed_a,
            indexed_b,
            embedding_cache,
            batch_id,
            similarity_fn=calculate_composite_similarity,
        )

        duration_ms = (time.time() * 1000) - start_ms
        self.obs_layer.log_consolidation_batch(
            batch_id=batch_id,
            processed=len(batch),
            divergences=divergence_count,
            writes=successful_writes,
            duration_ms=duration_ms,
        )


async def start_tier3_deferred_worker(
    storage: StorageFacade,
    consolidation_loop: ConsolidationLoop,
    sleep_interval: int = 5,
    batch_size: int = 10,
):
    """
    Background worker that continuously consumes and processes
    unconsolidated records flagged with tier3_deferred=True.
    """
    logger.info("Starting Tier-3 Deferred background worker...")
    while True:
        try:
            records = await storage.raw_log.fetch_unconsolidated(limit=100)
            deferred_records = [r for r in records if r.get("tier3_deferred")]

            if deferred_records:
                batch = deferred_records[:batch_size]
                logger.debug(
                    f"Worker fetched {len(deferred_records)} deferred records."
                )
                logger.info(f"Worker processing {len(batch)} deferred records.")
                await consolidation_loop.run_batch(batch)

                # Clear the tier3_deferred flag to prevent infinite loops on the same record
                for record in batch:
                    await storage.raw_log.clear_tier3_deferred(record["cmb_id"])
            else:
                await asyncio.sleep(sleep_interval)

        except asyncio.CancelledError:
            logger.info("Tier-3 Deferred worker cancelled, shutting down.")
            break
        except Exception as e:
            logger.error(f"Error in Tier-3 Deferred worker: {e}", exc_info=True)
            await asyncio.sleep(sleep_interval)
