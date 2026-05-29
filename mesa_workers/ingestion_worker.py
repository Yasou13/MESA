# MESA v0.4.0 — Phase 1 Part 2: Cold Path Ingestion Worker
# Asynchronous background worker that processes queued raw_logs entries
# through the full validation pipeline.
#
# Architecture:
#   Hot Path (router.py)  → INSERT into raw_logs (< 50ms, pure I/O)
#   Cold Path (this file) → BackgroundTask processes raw_logs entry:
#       1. Retrieve payload from raw_logs
#       2. Tier-1 ECOD anomaly detection
#       3. Tier-2 REBEL triple extraction
#       4. Tier-3 Dual-LLM consensus (ConsolidationLoop)
#       5. Graph commit via DAO (insert_memory + insert_edge)
#       6. Status update (queued → processing → processed | failed | rejected)
#
# Safety invariant:
#   ALL exceptions are caught within process_cold_path.
#   Failures update raw_logs status and log the error trace.
#   Nothing bubbles up to crash the FastAPI background task pool.
"""
Cold-path ingestion worker for the MESA v0.4.0 decoupled pipeline.

Consumes entries from the ``raw_logs`` staging table and runs them through
the full validation stack before committing to permanent graph storage.

Pipeline stages::

    1. Payload retrieval + status guard (skip if not 'queued')
    2. Status transition: queued → processing
    3. Tier-1 ECOD novelty gate (cosine fast-path + anomaly scoring)
    4. Tier-2 REBEL triple extraction (zero-cost HF pipeline)
    5. Tier-3 Dual-LLM consensus via ConsolidationLoop.run_batch()
    6. Graph commit: dao.insert_memory() + dao.insert_edge()
    7. Status transition: processing → processed | failed | rejected

Usage::

    from mesa_workers.ingestion_worker import process_cold_path

    background_tasks.add_task(process_cold_path, log_id, dao)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
import uuid
from typing import Any

from mesa_memory.config import config
from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.extraction.rebel_pipeline import RebelExtractor
from mesa_memory.valence.novelty import calculate_novelty_score
from mesa_storage.dao import MemoryDAO

logger = logging.getLogger("MESA_ColdPath")


# ---------------------------------------------------------------------------
# Module-level singletons — initialised lazily on first cold-path call
# ---------------------------------------------------------------------------

_rebel_extractor: RebelExtractor | None = None
MAX_CONCURRENT_WORKERS = asyncio.Semaphore(10)
MAX_TIER3_CONCURRENT = 3
_tier3_semaphore = asyncio.Semaphore(MAX_TIER3_CONCURRENT)


def _get_rebel_extractor() -> RebelExtractor | None:
    """Lazy-init the REBEL singleton.

    Returns ``None`` when ``MESA_REBEL_ENABLED`` is ``False``, preventing
    the 1.8 GB model download and eliminating Docker timeout / slow
    onboarding issues.
    """
    if not config.rebel_enabled:
        return None

    global _rebel_extractor
    if _rebel_extractor is None:
        _rebel_extractor = RebelExtractor()
    return _rebel_extractor


# ---------------------------------------------------------------------------
# Cold Path — main entry point
# ---------------------------------------------------------------------------


async def process_cold_path(
    log_id: int,
    dao: MemoryDAO,
    consolidation_loop: ConsolidationLoop | None = None,
) -> None:
    """Process a queued raw_logs entry through the full validation pipeline.

    This is the **cold-path worker** invoked as a ``BackgroundTask`` after
    the hot-path INSERT returns 202 Accepted.  It orchestrates:

        1. Payload retrieval and status guard.
        2. Tier-1 ECOD anomaly detection (novelty gate).
        3. Tier-2 REBEL triple extraction.
        4. Graph commit via DAO.
        5. Status finalisation.

    **Safety guarantee**: The entire workflow is wrapped in a top-level
    ``try/except Exception`` block.  All failures — LLM timeouts, API
    rate limits, ECOD/REBEL rejections, DB errors — are caught, logged,
    and reflected as a status update on the ``raw_logs`` row.  Nothing
    propagates to the FastAPI background task pool.

    Args:
        log_id: Primary key of the ``raw_logs`` row to process.
        dao: Initialised ``MemoryDAO`` instance.
    """
    t_start = time.monotonic()

    try:
        async with MAX_CONCURRENT_WORKERS:
            # ==============================================================
            # 1. RETRIEVE PAYLOAD + STATUS GUARD
            # ==============================================================
            raw_log = await dao.get_raw_log(log_id)

            if raw_log is None:
                logger.warning(
                    "COLD_PATH_SKIP | log_id=%d reason=not_found", log_id
                )
                return

            if raw_log["status"] != "queued":
                logger.debug(
                    "COLD_PATH_SKIP | log_id=%d reason=status_is_%s",
                    log_id,
                    raw_log["status"],
                )
                return

            payload: dict[str, Any] = raw_log["payload"]
            agent_id: str = payload.get("agent_id", "")
            session_id: str = payload.get("session_id", "__unset__")
            content: str = payload.get("content", "")
            metadata: dict = payload.get("metadata", {})

            if not agent_id or not content:
                await dao.update_raw_log_status(
                    log_id, "rejected", error_reason="missing_agent_id_or_content"
                )
                logger.warning(
                    "COLD_PATH_REJECTED | log_id=%d reason=missing_required_fields",
                    log_id,
                )
                return

            # ==============================================================
            # 2. STATUS → processing
            # ==============================================================
            await dao.update_raw_log_status(log_id, "processing")

            logger.info(
                "COLD_PATH_START | log_id=%d agent_id=%s content_len=%d",
                log_id,
                agent_id,
                len(content),
            )

            # ==============================================================
            # 3. TIER-1: ECOD ANOMALY DETECTION (Novelty Gate)
            # ==============================================================
            ecod_passed = await _run_ecod_gate(dao, agent_id, content)

            if not ecod_passed:
                await dao.update_raw_log_status(
                    log_id, "rejected", error_reason="ecod_novelty_below_threshold"
                )
                logger.info(
                    "COLD_PATH_REJECTED | log_id=%d reason=ecod_novelty_gate",
                    log_id,
                )
                return

            # ==============================================================
            # 4. TIER-2: REBEL TRIPLE EXTRACTION
            # ==============================================================
            triplets = await _run_rebel_extraction(content)

            # ==============================================================
            # 5. GRAPH COMMIT
            # ==============================================================
            if triplets:
                await _commit_triplets(
                    dao=dao,
                    agent_id=agent_id,
                    session_id=session_id,
                    content=content,
                    triplets=triplets,
                    log_id=log_id,
                )
            else:
                # No triplets extracted — still commit as a raw memory node
                await _commit_raw_memory(
                    dao=dao,
                    agent_id=agent_id,
                    session_id=session_id,
                    content=content,
                    log_id=log_id,
                )

            # ==============================================================
            # 5b. TIER-3: DUAL-LLM CONSENSUS (Backpressure-gated)
            # ==============================================================
            if consolidation_loop is not None:
                record = {
                    "id": str(log_id),
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "content": content,
                    "metadata": metadata,
                }
                try:
                    async with _tier3_semaphore:
                        await consolidation_loop.run_batch([record])
                    logger.debug(
                        "TIER3_CONSENSUS_DONE | log_id=%d agent_id=%s",
                        log_id,
                        agent_id,
                    )
                except Exception as t3_exc:
                    # Tier-3 failure must NOT block cold-path commit
                    logger.warning(
                        "TIER3_CONSENSUS_FAILED | log_id=%d error=%s",
                        log_id,
                        t3_exc,
                    )

            # ==============================================================
            # 6. STATUS → processed
            # ==============================================================
            await dao.update_raw_log_status(log_id, "processed")

            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            logger.info(
                "COLD_PATH_DONE | log_id=%d agent_id=%s triplets=%d elapsed_ms=%d",
                log_id,
                agent_id,
                len(triplets),
                elapsed_ms,
            )

    except Exception as exc:
        # ==============================================================
        # SAFETY NET — catch ALL exceptions, update status, log trace
        # ==============================================================
        error_type = type(exc).__name__
        error_msg = f"{error_type}: {exc}"

        # Truncate error reason to prevent oversized status fields
        truncated_reason = error_msg[:500]

        try:
            await dao.update_raw_log_status(
                log_id, "failed", error_reason=truncated_reason
            )
        except Exception as status_exc:
            # Even the status update failed — log but never raise
            logger.critical(
                "COLD_PATH_STATUS_UPDATE_FAILED | log_id=%d "
                "original_error=%s status_error=%s",
                log_id,
                error_msg,
                status_exc,
            )

        logger.error(
            "COLD_PATH_FAILED | log_id=%d error=%s\n%s",
            log_id,
            error_msg,
            traceback.format_exc(),
        )
        # CRITICAL: Do NOT re-raise — protect the BG task pool


# ---------------------------------------------------------------------------
# Stage 3: ECOD Novelty Gate
# ---------------------------------------------------------------------------


async def _run_ecod_gate(
    dao: MemoryDAO,
    agent_id: str,
    content: str,
) -> bool:
    """Run the Tier-1 ECOD anomaly detection gate.

    In cold-start conditions (no existing embeddings), the gate always
    passes to allow the memory pool to bootstrap.  Once sufficient
    history exists, ECOD scores the content embedding against the
    existing pool.

    Args:
        dao: Initialised MemoryDAO instance.
        agent_id: Agent scope for existing embedding retrieval.
        content: Raw text content to evaluate.

    Returns:
        ``True`` if the content passes the novelty gate (is novel).
    """
    try:
        import numpy as np

        from mesa_memory.config import config

        # Retrieve existing embeddings for this agent's memory pool
        existing_memories = await dao.get_memories(agent_id, limit=500)

        if len(existing_memories) < 2:
            # Cold-start: always admit — not enough data for ECOD
            logger.debug(
                "ECOD_COLD_START | agent_id=%s pool_size=%d — admitting",
                agent_id,
                len(existing_memories),
            )
            return True

        # B-2 FIX: Offload CPU-bound hashing + numpy construction to
        # a thread-pool executor to prevent event loop starvation.
        loop = asyncio.get_running_loop()

        def _build_embeddings():
            content_emb = np.array(_hash_embedding_sync(content, dim=8))
            existing_embs = np.array(
                [_hash_embedding_sync(m.get("entity_name", ""), dim=8) for m in existing_memories]
            )
            return content_emb, existing_embs

        content_embedding, existing_embeddings = await loop.run_in_executor(
            None, _build_embeddings
        )

        is_novel = await calculate_novelty_score(
            new_embedding=content_embedding,
            existing_embeddings=existing_embeddings,
            cosine_threshold=config.bootstrap_cosine_threshold,
        )
        return is_novel

    except ImportError:
        # numpy/pyod not available — degrade gracefully, always pass
        logger.warning("ECOD_IMPORT_ERROR | dependencies missing — gate bypassed")
        return True
    except Exception as exc:
        # ECOD failure should not block ingestion — log and pass
        logger.warning(
            "ECOD_GATE_ERROR | agent_id=%s error=%s — gate bypassed",
            agent_id,
            exc,
        )
        return True


def _hash_embedding_sync(text: str, dim: int = 8) -> list[float]:
    """Generate a deterministic pseudo-embedding from text via hashing.

    This is a lightweight proxy for the full embedding model, used in
    the cold-path ECOD gate where loading a transformer is too expensive.
    The hash is spread across ``dim`` float channels via modular arithmetic.

    **Must be called inside ``run_in_executor``** — the ``hashlib.sha256``
    call and list construction are CPU-bound and will starve the event loop
    if invoked on the main thread under high concurrency (B-2 fix).
    """
    import hashlib

    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    values = []
    for i in range(dim):
        segment = h[i * 4 : (i + 1) * 4]
        values.append(int(segment, 16) / 65535.0)
    return values


# ---------------------------------------------------------------------------
# Stage 4: Triple Extraction (REBEL or LLM fallback)
# ---------------------------------------------------------------------------

# -- Zero-shot prompt for LLM-only triple extraction -----------------------
_LLM_TRIPLET_PROMPT = """Extract all factual relationships from the text below as (subject, predicate, object) triplets.

Rules:
- Output ONLY a JSON array of objects with keys "head", "relation", "tail".
- Each value must be a short noun phrase or verb phrase — no full sentences.
- If no relationships can be extracted, return an empty array [].
- Do NOT include any explanation, markdown fences, or commentary.

Text:
{text}

JSON:"""


async def _run_rebel_extraction(content: str) -> list[dict[str, str]]:
    """Dispatch triple extraction to REBEL or LLM fallback.

    Strategy:
        * ``MESA_REBEL_ENABLED=True``  → REBEL HF pipeline (thread-pool)
        * ``MESA_REBEL_ENABLED=False`` → LLM zero-shot via Tier-3 adapter

    Both paths return the identical ``[{head, relation, tail}, ...]`` format
    consumed by ``_commit_triplets``.

    Args:
        content: Raw text content to extract triples from.

    Returns:
        List of ``{head, relation, tail}`` dicts.
    """
    extractor = _get_rebel_extractor()

    if extractor is not None:
        return await _run_rebel_extraction_impl(extractor, content)

    # REBEL disabled — use LLM fallback
    return await _run_llm_triplet_extraction(content)


async def _run_rebel_extraction_impl(
    extractor: RebelExtractor, content: str
) -> list[dict[str, str]]:
    """Execute the REBEL HF pipeline in a thread-pool executor."""
    try:
        loop = asyncio.get_running_loop()
        triplets = await loop.run_in_executor(
            None, extractor.extract_triplets, content
        )
        logger.debug(
            "REBEL_EXTRACT | content_len=%d triplets=%d",
            len(content),
            len(triplets),
        )
        return triplets

    except ImportError:
        logger.warning("REBEL_IMPORT_ERROR | transformers not installed — skipping")
        return []
    except Exception as exc:
        logger.warning("REBEL_EXTRACT_ERROR | error=%s — skipping", exc)
        return []


async def _run_llm_triplet_extraction(content: str) -> list[dict[str, str]]:
    """LLM-only fallback: extract triplets via zero-shot Tier-3 prompt.

    Uses the existing ``AdapterFactory`` (Groq / Llama-3 / Ollama) to call
    the configured LLM with a structured JSON extraction prompt.  Output is
    normalised to the same ``{head, relation, tail}`` dict format that
    ``_commit_triplets`` expects.

    This path avoids downloading the 1.8 GB REBEL model entirely.
    """
    try:
        from mesa_memory.adapter.factory import AdapterFactory

        adapter = AdapterFactory.get_adapter()

        # Truncate to ~2000 chars to stay within Tier-3 context window
        truncated = content[:2000]
        prompt = _LLM_TRIPLET_PROMPT.format(text=truncated)

        raw_response = await adapter.acomplete(
            prompt,
            max_tokens=512,
            temperature=0.0,
        )

        # Parse the JSON array from the LLM response
        triplets = _parse_llm_triplet_response(raw_response)

        logger.debug(
            "LLM_TRIPLET_EXTRACT | content_len=%d triplets=%d",
            len(content),
            len(triplets),
        )
        return triplets

    except Exception as exc:
        logger.warning(
            "LLM_TRIPLET_EXTRACT_ERROR | error=%s — returning empty", exc
        )
        return []


def _parse_llm_triplet_response(raw: str) -> list[dict[str, str]]:
    """Parse and validate the LLM JSON response into normalised triplets.

    Handles common LLM output quirks: markdown fences, trailing commas,
    extra prose around the JSON array.

    Returns:
        List of ``{head, relation, tail}`` dicts.  Invalid entries are
        silently dropped.
    """
    from mesa_memory.adapter.live import OpenAICompatibleAdapter

    cleaned = OpenAICompatibleAdapter._sanitize_json(raw)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("LLM_TRIPLET_PARSE_FAILED | raw=%s", raw[:200])
        return []

    if not isinstance(parsed, list):
        parsed = [parsed]

    triplets: list[dict[str, str]] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        head = str(entry.get("head", entry.get("subject", ""))).strip()
        relation = str(entry.get("relation", entry.get("predicate", ""))).strip()
        tail = str(entry.get("tail", entry.get("object", ""))).strip()
        if head and relation and tail:
            triplets.append({"head": head, "relation": relation, "tail": tail})

    return triplets


# ---------------------------------------------------------------------------
# Stage 5a: Commit extracted triplets to graph storage
# ---------------------------------------------------------------------------


async def _commit_triplets(
    *,
    dao: MemoryDAO,
    agent_id: str,
    session_id: str,
    content: str,
    triplets: list[dict[str, str]],
    log_id: int,
) -> None:
    """Commit extracted REBEL triplets as graph nodes + edges via DAO.

    For each triplet ``{head, relation, tail}``:
        1. Insert head entity as a graph node via ``dao.insert_memory``.
        2. Insert tail entity as a graph node via ``dao.insert_memory``.
        3. Link head → tail via ``dao.insert_edge``.

    Uses zero-vectors for embeddings since the hot-path semantic vectors
    are stored separately in LanceDB during the full embedding pass.

    Args:
        dao: Initialised MemoryDAO instance.
        agent_id: Agent scope for RLS enforcement.
        session_id: Session scope within the agent.
        content: Original content (used for node context).
        triplets: List of ``{head, relation, tail}`` dicts from REBEL.
        log_id: raw_logs primary key (used for node context tagging).
    """
    zero_embedding = [0.0] * 8  # Placeholder — real embeddings via cold-path Phase 2

    for triplet in triplets:
        head = triplet.get("head", "").strip()
        relation = triplet.get("relation", "").strip()
        tail = triplet.get("tail", "").strip()

        if not head or not tail:
            logger.debug(
                "SKIP_TRIPLET | log_id=%d reason=empty_entity head=%r tail=%r",
                log_id,
                head,
                tail,
            )
            continue

        try:
            # Insert head entity node
            head_node_id = await dao.insert_memory(
                agent_id,
                entity_name=head,
                content=f"[raw_log:{log_id}] {head}",
                embedding=zero_embedding,
                node_type="ENTITY",
                session_id=session_id,
            )

            # Insert tail entity node
            tail_node_id = await dao.insert_memory(
                agent_id,
                entity_name=tail,
                content=f"[raw_log:{log_id}] {tail}",
                embedding=zero_embedding,
                node_type="ENTITY",
                session_id=session_id,
            )

            # Link head → tail via extracted relation
            await dao.insert_edge(
                agent_id,
                source_id=head_node_id,
                target_id=tail_node_id,
                relation_type=relation or "RELATED_TO",
                weight=1.0,
            )

            logger.debug(
                "TRIPLET_COMMITTED | log_id=%d head=%s rel=%s tail=%s",
                log_id,
                head,
                relation,
                tail,
            )

        except Exception as exc:
            # Individual triplet failure should not abort the batch
            logger.warning(
                "TRIPLET_COMMIT_FAILED | log_id=%d head=%s tail=%s error=%s",
                log_id,
                head,
                tail,
                exc,
            )


# ---------------------------------------------------------------------------
# Stage 5b: Commit raw memory node (no triplets extracted)
# ---------------------------------------------------------------------------


async def _commit_raw_memory(
    *,
    dao: MemoryDAO,
    agent_id: str,
    session_id: str,
    content: str,
    log_id: int,
) -> None:
    """Commit the raw content as a plain memory node when REBEL yields no triples.

    This ensures every ingested payload has at least one searchable node
    in the graph, even if no structured relations were extracted.

    Args:
        dao: Initialised MemoryDAO instance.
        agent_id: Agent scope for RLS enforcement.
        session_id: Session scope within the agent.
        content: Original content text.
        log_id: raw_logs primary key.
    """
    zero_embedding = [0.0] * 8

    node_id = await dao.insert_memory(
        agent_id,
        entity_name=content[:256],
        content=content,
        embedding=zero_embedding,
        node_type="MEMORY",
        session_id=session_id,
        content_hash=None,
    )

    logger.debug(
        "RAW_MEMORY_COMMITTED | log_id=%d node_id=%s agent_id=%s",
        log_id,
        node_id,
        agent_id,
    )
