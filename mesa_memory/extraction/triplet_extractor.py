"""
Triplet Extractor — REBEL zero-cost pipeline, LLM fallback, and bisection retry.

Extracted from ``loop.py`` to enforce the Single Responsibility Principle.
This module owns:
- REBEL zero-cost extraction with LLM fallback.
- Single-record 1:1 LLM extraction.
- Recursive bisection retry (Layer 3) for corrupted sub-batches.
- Batch prompt construction with positional tagging.
"""

import asyncio
import functools
import json
import logging
from typing import Optional

from pydantic import ValidationError

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import config
from mesa_memory.consolidation.parser import (
    BATCH_PROMPT_A_TEMPLATE,
    BATCH_PROMPT_B_TEMPLATE,
    PROMPT_A_TEMPLATE,
    PROMPT_B_TEMPLATE,
    BatchResponseParser,
    _estimate_salience,
)
from mesa_memory.consolidation.schemas import BatchExtractionResponse, ExtractedTriplet
from mesa_memory.extraction.rebel_pipeline import RebelExtractor
from mesa_memory.utils import _strip_markdown_json

logger = logging.getLogger("MESA_Consolidation")


class TripletExtractor:
    """Manages the full extraction lifecycle: REBEL → LLM batch → bisection → 1:1.

    Delegates response parsing to :class:`BatchResponseParser` from ``parser.py``.
    """

    def __init__(
        self,
        llm_a: BaseUniversalLLMAdapter,
        llm_b: BaseUniversalLLMAdapter,
    ):
        self.llm_a = llm_a
        self.llm_b = llm_b
        self.rebel_extractor = RebelExtractor()
        self._parser = BatchResponseParser()

    # -------------------------------------------------------------------
    # Batch prompt construction helpers
    # -------------------------------------------------------------------

    @staticmethod
    def sort_by_salience(records: list[dict]) -> list[dict]:
        """Sort records so high-salience items occupy edge positions.

        Strategy: sort descending by salience, then interleave — highest go
        to index 0, N-1, 1, N-2, ... pushing lowest-salience to the middle.
        """
        scored = sorted(records, key=_estimate_salience, reverse=True)
        result: list[dict] = [None] * len(scored)  # type: ignore[list-item]
        lo, hi = 0, len(scored) - 1
        for idx, record in enumerate(scored):
            if idx % 2 == 0:
                result[lo] = record
                lo += 1
            else:
                result[hi] = record
                hi -= 1
        return result

    @staticmethod
    def build_records_block(sub_batch: list[dict]) -> str:
        """Build the positionally-tagged records block for a batch prompt.

        Includes:
        - Explicit ``=== RECORD N ===`` / ``=== END RECORD N ===`` delimiters
          (LitM Layer 1: Positional Tagging).
        - Attention-reset checkpoints every ``ANCHOR_INTERVAL`` records
          (LitM Layer 4: Interleaved Anchor Tokens).
        """
        parts: list[str] = []
        for i, record in enumerate(sub_batch):
            if i > 0 and i % config.anchor_interval == 0:
                parts.append(
                    f"--- CHECKPOINT: You have processed records 0 through {i - 1}. "
                    f"Continue with record {i}. ---\n"
                )
            content = record.get("content_payload", "")
            source = record.get("source", "")
            parts.append(
                f"=== RECORD {i} ===\n"
                f"<CONTENT>\n{content}\n</CONTENT>\n"
                f"Source: {source}\n"
                f"=== END RECORD {i} ==="
            )
        return "\n\n".join(parts)

    # -------------------------------------------------------------------
    # Single-record fallback
    # -------------------------------------------------------------------

    async def _single_record_extract(
        self,
        record: dict,
        llm: BaseUniversalLLMAdapter,
        template: str,
    ) -> Optional[dict]:
        """Extract a single triplet using legacy 1:1 prompting.

        Used as terminal fallback when batch parsing fails for individual
        records. Returns a plain dict ``{head, relation, tail}`` or None.
        """
        content = record.get("content_payload", "")
        source = record.get("source", "")
        prompt = template.format(content=content, source=source)

        loop = asyncio.get_running_loop()
        try:
            raw = await loop.run_in_executor(
                None,
                functools.partial(llm.complete, prompt),
            )
            cleaned = _strip_markdown_json(raw) if isinstance(raw, str) else ""
            return json.loads(cleaned) if cleaned else None
        except (json.JSONDecodeError, TypeError, Exception) as exc:
            logger.warning(f"Single-record fallback failed: {exc}")
            return None

    # -------------------------------------------------------------------
    # Bisection retry (Layer 3)
    # -------------------------------------------------------------------

    async def _retry_with_bisection(
        self,
        sub_batch: list[dict],
        batch_template: str,
        fallback_template: str,
        llm: BaseUniversalLLMAdapter,
        depth: int = 0,
    ) -> dict[int, ExtractedTriplet]:
        """Recursive bisection retry for corrupted sub-batches (Layer 3).

        Splits the failed sub-batch in half and retries each half. At max
        retry depth, falls back to 1:1 single-record extraction.

        Returns a dict mapping original sub-batch index → ExtractedTriplet.
        """
        results: dict[int, ExtractedTriplet] = {}

        if depth >= config.truncation_max_retries:
            # Terminal: 1:1 fallback for every record
            for i, record in enumerate(sub_batch):
                trip = await self._single_record_extract(
                    record,
                    llm,
                    fallback_template,
                )
                if trip and trip.get("head"):
                    results[i] = ExtractedTriplet(
                        record_index=i,
                        head=trip["head"],
                        relation=trip.get("relation", ""),
                        tail=trip.get("tail", ""),
                    )
            return results

        mid = len(sub_batch) // 2
        halves = [sub_batch[:mid], sub_batch[mid:]]
        offset = 0

        for half in halves:
            if not half:
                offset += 0
                continue
            try:
                records_block = self.build_records_block(half)
                prompt = batch_template.format(records_block=records_block)
                loop = asyncio.get_running_loop()
                raw = await loop.run_in_executor(
                    None,
                    functools.partial(
                        llm.complete,
                        prompt,
                        BatchExtractionResponse,
                    ),
                )
                response = self._parser.parse(raw, len(half))
                for triplet in response.triplets:
                    if 0 <= triplet.record_index < len(half):
                        results[offset + triplet.record_index] = triplet
            except (ValueError, ValidationError, Exception) as exc:
                logger.warning(
                    f"Bisection retry depth={depth} failed ({len(half)} records): {exc}"
                )
                sub_results = await self._retry_with_bisection(
                    half,
                    batch_template,
                    fallback_template,
                    llm,
                    depth + 1,
                )
                for local_idx, triplet in sub_results.items():
                    results[offset + local_idx] = triplet

            offset += len(half)

        return results

    # -------------------------------------------------------------------
    # Full extraction pipeline
    # -------------------------------------------------------------------

    async def extract_batch(
        self,
        sorted_batch: list[dict],
    ) -> tuple[dict[int, ExtractedTriplet], dict[int, ExtractedTriplet]]:
        """Run the full extraction pipeline over a sorted batch.

        1. Zero-cost REBEL extraction for all records.
        2. LLM fallback (dual-LLM) for records REBEL could not handle.
        3. Bisection retry and 1:1 fallback for parse failures.

        Returns ``(indexed_a, indexed_b)`` — dicts mapping global batch
        index → ExtractedTriplet from each LLM perspective.
        """
        loop = asyncio.get_running_loop()

        indexed_a: dict[int, ExtractedTriplet] = {}
        indexed_b: dict[int, ExtractedTriplet] = {}
        missing_a = list(range(len(sorted_batch)))
        missing_b = list(range(len(sorted_batch)))

        # --- Phase: Zero-Cost REBEL extraction ---
        for idx, record in enumerate(sorted_batch):
            try:
                triplets = await loop.run_in_executor(
                    None,
                    self.rebel_extractor.extract_triplets,
                    record.get("content_payload", ""),
                )
                if triplets:
                    indexed_a[idx] = ExtractedTriplet(
                        record_index=idx,
                        head=triplets[0]["head"],
                        relation=triplets[0]["relation"],
                        tail=triplets[0]["tail"],
                    )
                    indexed_b[idx] = ExtractedTriplet(
                        record_index=idx,
                        head=triplets[0]["head"],
                        relation=triplets[0]["relation"],
                        tail=triplets[0]["tail"],
                    )
                    missing_a.remove(idx)
                    missing_b.remove(idx)
            except Exception as e:
                logger.warning(f"REBEL extraction failed for record {idx}: {e}")

        # --- Phase: LLM fallback for missing records ---
        if missing_a:
            fallback_batch = [sorted_batch[i] for i in missing_a]
            logger.info(f"Falling back to LLMs for {len(fallback_batch)} records.")

            fallback_records_block = self.build_records_block(fallback_batch)
            fb_prompt_a = BATCH_PROMPT_A_TEMPLATE.format(
                records_block=fallback_records_block
            )
            fb_prompt_b = BATCH_PROMPT_B_TEMPLATE.format(
                records_block=fallback_records_block
            )

            raw_a, raw_b = await asyncio.gather(
                loop.run_in_executor(
                    None,
                    functools.partial(
                        self.llm_a.complete, fb_prompt_a, BatchExtractionResponse
                    ),
                ),
                loop.run_in_executor(
                    None,
                    functools.partial(
                        self.llm_b.complete, fb_prompt_b, BatchExtractionResponse
                    ),
                ),
            )

            try:
                response_a = self._parser.parse(raw_a, len(fallback_batch))
                fb_indexed_a, fb_missing_a = self._parser.audit_coverage(
                    response_a, len(fallback_batch)
                )
            except ValueError:
                fb_indexed_a = await self._retry_with_bisection(
                    fallback_batch,
                    BATCH_PROMPT_A_TEMPLATE,
                    PROMPT_A_TEMPLATE,
                    self.llm_a,
                )
                fb_missing_a = []

            try:
                response_b = self._parser.parse(raw_b, len(fallback_batch))
                fb_indexed_b, fb_missing_b = self._parser.audit_coverage(
                    response_b, len(fallback_batch)
                )
            except ValueError:
                fb_indexed_b = await self._retry_with_bisection(
                    fallback_batch,
                    BATCH_PROMPT_B_TEMPLATE,
                    PROMPT_B_TEMPLATE,
                    self.llm_b,
                )
                fb_missing_b = []

            # Map fallback results back to global indices
            for local_idx, triplet in fb_indexed_a.items():
                global_idx = missing_a[local_idx]
                triplet.record_index = global_idx
                indexed_a[global_idx] = triplet

            for local_idx, triplet in fb_indexed_b.items():
                global_idx = missing_b[local_idx]
                triplet.record_index = global_idx
                indexed_b[global_idx] = triplet

            for local_idx in fb_missing_a:
                global_idx = missing_a[local_idx]
                trip = await self._single_record_extract(
                    sorted_batch[global_idx], self.llm_a, PROMPT_A_TEMPLATE
                )
                if trip and trip.get("head"):
                    indexed_a[global_idx] = ExtractedTriplet(
                        record_index=global_idx,
                        head=trip["head"],
                        relation=trip.get("relation", ""),
                        tail=trip.get("tail", ""),
                    )

            for local_idx in fb_missing_b:
                global_idx = missing_b[local_idx]
                trip = await self._single_record_extract(
                    sorted_batch[global_idx], self.llm_b, PROMPT_B_TEMPLATE
                )
                if trip and trip.get("head"):
                    indexed_b[global_idx] = ExtractedTriplet(
                        record_index=global_idx,
                        head=trip["head"],
                        relation=trip.get("relation", ""),
                        tail=trip.get("tail", ""),
                    )

        return indexed_a, indexed_b
