# MESA v0.6.1 — Phase 1 Part 2: Cold Path Ingestion Worker
# Asynchronous background worker that processes queued raw_logs entries
# through the full validation pipeline.
#
# Architecture:
#   Hot Path (router.py)  → durable dispatch queue admission (<50ms, pure I/O)
#   Cold Path (worker runtime) → claims and processes raw_logs entries:
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
#   Nothing bubbles up to crash the worker supervisor.
"""
Cold-path ingestion worker for the MESA v0.6.1 decoupled pipeline.

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

    await process_cold_path(log_id, agent_id, dao)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import traceback
from pathlib import Path
from typing import Any

import structlog
from tenacity import RetryError, retry, stop_after_attempt, wait_exponential

from mesa_memory.config import config
from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.extraction.rebel_pipeline import RebelExtractor
from mesa_memory.observability.logger import setup_logging
from mesa_memory.valence.novelty import calculate_novelty_score
from mesa_storage.dao import MemoryDAO

# Configure logging for the worker process
setup_logging()

logger = structlog.get_logger("MESA_ColdPath")

# ---------------------------------------------------------------------------
# Module-level singletons — initialised lazily on first cold-path call
# ---------------------------------------------------------------------------

_rebel_extractor: RebelExtractor | None = None
MAX_CONCURRENT_WORKERS = asyncio.Semaphore(10)
MAX_TIER3_CONCURRENT = 3
_tier3_semaphore = asyncio.Semaphore(MAX_TIER3_CONCURRENT)
_TRACE_ROOT = Path("/storage/mesa-lab").resolve()


def _write_cold_path_trace(message: str) -> None:
    """Write optional diagnostics without accepting arbitrary output paths."""
    configured = os.getenv("MESA_COLD_PATH_TRACE_PATH")
    if configured:
        candidate = Path(configured).resolve()
        try:
            candidate.relative_to(_TRACE_ROOT)
        except ValueError:
            logger.warning("COLD_PATH_TRACE_DISABLED | reason=path_outside_lab_root")
            return
        candidate.parent.mkdir(parents=True, exist_ok=True)
    else:
        logger.debug("COLD_PATH_TRACE_DISABLED | reason=no_explicit_lab_path")
        return
    with candidate.open("a", encoding="utf-8") as trace:
        trace.write(message + "\n")


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
    agent_id: str,
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
    claim: dict[str, Any] | None = None

    async def _transition(
        status: str,
        *,
        error_reason: str | None = None,
        target_agent_id: str | None = None,
    ) -> None:
        """Use a fenced transition for the real DAO; retain mock compatibility."""
        target_agent = target_agent_id or agent_id
        if claim is not None:
            transitioned = await dao.transition_claimed_raw_log(
                target_agent,
                log_id,
                worker_id=claim["claimed_by"],
                claim_token=claim["claim_token"],
                status=status,
                error_reason=error_reason,
            )
            if not transitioned:
                logger.warning(
                    "COLD_PATH_FENCE_LOST | log_id=%d status=%s", log_id, status
                )
            return
        if error_reason is None:
            await dao.update_raw_log_status(target_agent, log_id, status)
        else:
            await dao.update_raw_log_status(
                target_agent, log_id, status, error_reason=error_reason
            )

    _write_cold_path_trace(f"START {log_id}")
    try:
        logger.info("COLD_PATH_DEBUG | Entering try block", log_id=log_id)
        _write_cold_path_trace(f"BEFORE SEMAPHORE {log_id}")
        async with MAX_CONCURRENT_WORKERS:
            _write_cold_path_trace(f"INSIDE SEMAPHORE {log_id}")
            # ==============================================================
            # 1. RETRIEVE PAYLOAD + STATUS GUARD
            # ==============================================================
            logger.info("COLD_PATH_DEBUG | Starting get_raw_log", log_id=log_id)
            _write_cold_path_trace(f"BEFORE GET_RAW_LOG {log_id}")
            if type(dao) is MemoryDAO:
                claim = await dao.claim_raw_log(agent_id, log_id, worker_id="cold-path")
                raw_log = claim
            else:
                raw_log = await dao.get_raw_log(agent_id, log_id)
            _write_cold_path_trace(f"AFTER GET_RAW_LOG {log_id}")
            logger.info("COLD_PATH_DEBUG | Finished get_raw_log", log_id=log_id)
            _write_cold_path_trace(f"RAW_LOG {raw_log}")

            if raw_log is None:
                _write_cold_path_trace("RETURNING NOT FOUND")
                logger.warning("COLD_PATH_SKIP | log_id=%d reason=not_found", log_id)
                return

            if claim is None and raw_log["status"] != "DEFERRED":
                _write_cold_path_trace(f"RETURNING WRONG STATUS {raw_log['status']}")
                logger.debug(
                    "COLD_PATH_SKIP | log_id=%d reason=status_is_%s",
                    log_id,
                    raw_log["status"],
                )
                return

            payload: dict[str, Any] = raw_log["payload"]
            payload_agent_id: str = payload.get("agent_id", "")
            session_id: str = payload.get("session_id", "__unset__")
            content: str = payload.get("content", "")
            metadata: dict = payload.get("metadata", {})

            if not payload_agent_id or not content:
                await _transition(
                    "rejected",
                    error_reason="missing_agent_id_or_content",
                    target_agent_id=agent_id,
                )
                logger.warning(
                    "COLD_PATH_REJECTED | log_id=%d reason=missing_required_fields",
                    log_id,
                )
                return

            # ==============================================================
            # 2. STATUS → processing
            # ==============================================================
            if claim is None:
                await _transition("processing", target_agent_id=payload_agent_id)

            logger.info(
                "COLD_PATH_START | log_id=%d agent_id=%s content_len=%d",
                log_id,
                payload_agent_id,
                len(content),
            )

            logger.info("COLD_PATH_DEBUG | Starting ECOD check", log_id=log_id)
            # ==============================================================
            # 3. TIER-1: ECOD ANOMALY DETECTION (Novelty Gate)
            # ==============================================================
            ecod_passed = await _run_ecod_gate(dao, payload_agent_id, content)

            if not ecod_passed:
                await _transition(
                    "rejected",
                    error_reason="ecod_novelty_below_threshold",
                    target_agent_id=payload_agent_id,
                )
                logger.info(
                    "COLD_PATH_REJECTED | log_id=%d reason=ecod_novelty_gate",
                    log_id,
                )
                return

            _write_cold_path_trace(f"BEFORE REBEL {log_id}")
            # ==============================================================
            # 4. TIER-2: REBEL TRIPLE EXTRACTION
            # ==============================================================
            logger.info("COLD_PATH_DEBUG | Starting REBEL check", log_id=log_id)
            triplets = await _run_rebel_extraction(content)
            _write_cold_path_trace(f"AFTER REBEL {log_id}")

            # ==============================================================
            # 4. TIER-3 (Consensus) or GRAPH COMMIT
            # ==============================================================
            logger.info("COLD_PATH_DEBUG | Starting Commit", log_id=log_id)
            _write_cold_path_trace(f"BEFORE COMMIT {log_id}")
            if triplets:
                await _commit_triplets(
                    dao=dao,
                    agent_id=payload_agent_id,
                    session_id=session_id,
                    content=content,
                    triplets=triplets,
                    log_id=log_id,
                )
            else:
                # No triplets extracted — still commit as a raw memory node
                await _commit_raw_memory(
                    dao=dao,
                    agent_id=payload_agent_id,
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
                    "agent_id": payload_agent_id,
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
                        payload_agent_id,
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
            await _transition("processed", target_agent_id=payload_agent_id)

            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            logger.info(
                "COLD_PATH_DONE | log_id=%d agent_id=%s triplets=%d elapsed_ms=%d",
                log_id,
                payload_agent_id,
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
            await _transition(
                "failed", error_reason=truncated_reason, target_agent_id=agent_id
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
                agent_id,  # type: ignore[no-untyped-def]
                len(existing_memories),
            )
            return True

        # B-2 FIX: Offload CPU-bound hashing + numpy construction to
        # a thread-pool executor to prevent event loop starvation.
        loop = asyncio.get_running_loop()

        def _build_embeddings():
            content_emb = np.array(_hash_embedding_sync(content, dim=8))
            existing_embs = np.array(
                [
                    _hash_embedding_sync(m.get("entity_name", ""), dim=8)
                    for m in existing_memories
                ]
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

# ---------------------------------------------------------------------------
# Zero-shot prompt templates — language-aware extraction
# ---------------------------------------------------------------------------

_ENGLISH_TRIPLET_PROMPT = """\
Extract all factual relationships from the text below as (subject, predicate, object) triplets.

Rules:
- Output ONLY a JSON array of objects with keys "subject", "predicate", "object", "confidence".
- "confidence" is a float between 0.0 and 1.0 indicating how certain you are that the relationship is factually correct and clearly stated in the text. Use 0.9-1.0 for explicit statements, 0.5-0.8 for inferred or ambiguous relationships, and below 0.5 for speculative or weakly supported claims.
- Each value for subject/predicate/object must be a short noun phrase or verb phrase — no full sentences.
- If no relationships can be extracted, return an empty array [].
- Do NOT include any explanation, markdown fences, or commentary.

Text:
{text}

JSON:"""

_TURKISH_TRIPLET_PROMPT = """\
Aşağıdaki Türkçe metinden tüm olgusal ilişkileri (özne, yüklem, nesne) üçlüleri olarak çıkar.

### Kurallar:
1. YALNIZCA aşağıdaki formatta bir JSON dizisi üret:
   [{{"subject": "...", "predicate": "...", "object": "...", "confidence": 0.95}}]
2. "subject" → özne (kişi, kurum, kanun maddesi, kavram).
3. "predicate" → yüklem (eylem veya ilişki: "düzenler", "yürürlüğe girer", "kapsar", "bağlıdır", "öngörür", "yasaklar" vb.).
4. "object" → nesne (etkilenen varlık, konu, hüküm).
5. "confidence" → 0.0 ile 1.0 arasında bir ondalık sayı. İlişkinin metinde ne kadar açık ve kesin ifade edildiğini belirtir. Açık ifadeler için 0.9-1.0, çıkarımsal veya belirsiz ilişkiler için 0.5-0.8, spekülatif veya zayıf destekli iddialar için 0.5'in altında kullan.
6. Her değer kısa bir isim veya fiil öbeği olmalı — tam cümle YAZMA.
7. Türkçe karakterleri (ç, ğ, ı, ö, ş, ü) koru — ASCII'ye dönüştürme.
8. Eğer hiçbir ilişki çıkarılamıyorsa boş dizi döndür: []
9. JSON dışında AÇIKLAMA, markdown bloğu veya yorum EKLEME.

### Örnekler:
Metin: "6698 sayılı Kişisel Verilerin Korunması Kanunu, veri sorumlularının yükümlülüklerini düzenler."
Çıktı: [{{"subject": "6698 sayılı KVKK", "predicate": "düzenler", "object": "veri sorumlusu yükümlülükleri", "confidence": 0.97}}]

Metin: "Anayasa Mahkemesi, bireysel başvuruları inceler ve karara bağlar."
Çıktı: [{{"subject": "Anayasa Mahkemesi", "predicate": "inceler", "object": "bireysel başvurular", "confidence": 0.95}}, {{"subject": "Anayasa Mahkemesi", "predicate": "karara bağlar", "object": "bireysel başvurular", "confidence": 0.95}}]

### Metin:
{text}

### JSON:"""

# Prompt registry — keyed by MESA_EXTRACTION_LANG
_PROMPT_REGISTRY: dict[str, str] = {
    "en": _ENGLISH_TRIPLET_PROMPT,
    "tr": _TURKISH_TRIPLET_PROMPT,
}


def _get_extraction_prompt(text: str) -> str:
    """Select the language-appropriate extraction prompt and inject the text.

    Falls back to English if ``config.extraction_lang`` is not in the
    registry.  Truncates input to ~2000 chars to stay within Tier-3
    context windows.
    """
    lang = config.extraction_lang.lower().strip()
    template = _PROMPT_REGISTRY.get(lang, _ENGLISH_TRIPLET_PROMPT)

    if lang not in _PROMPT_REGISTRY:
        logger.warning(
            "EXTRACTION_LANG_UNKNOWN | lang=%r — falling back to English prompt",
            lang,
        )

    # Truncate to ~2000 chars to stay within Tier-3 context window
    truncated = text[:2000]
    return template.format(text=truncated)


async def _run_rebel_extraction(content: str) -> list[dict[str, str]]:
    """Dispatch triple extraction to REBEL or LLM fallback.

    Strategy:
        * ``MESA_REBEL_ENABLED=True``  → REBEL HF pipeline (thread-pool)
        * ``MESA_REBEL_ENABLED=False`` → LLM zero-shot via Tier-3 adapter
          with language-aware prompt (Turkish or English).

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

    # REBEL disabled — use language-aware LLM extraction
    return await _run_llm_triplet_extraction(content)


async def _run_rebel_extraction_impl(
    extractor: RebelExtractor, content: str
) -> list[dict[str, str]]:
    """Execute the REBEL HF pipeline in a thread-pool executor."""
    try:
        loop = asyncio.get_running_loop()
        triplets = await loop.run_in_executor(None, extractor.extract_triplets, content)
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
    """LLM-only extraction: extract triplets via language-aware zero-shot prompt.

    Uses the ``AdapterFactory`` (Groq / Llama-3 / Ollama) to call the
    configured LLM with a structured JSON extraction prompt.  The prompt
    is selected based on ``config.extraction_lang`` — currently supports
    ``"tr"`` (Turkish legal/formal) and ``"en"`` (English general).

    Output is normalised to the canonical ``{head, relation, tail}`` dict
    format consumed by ``_commit_triplets``.

    This path avoids downloading the 1.8 GB REBEL model entirely.
    """
    try:
        from mesa_memory.adapter.factory import AdapterFactory

        adapter = AdapterFactory.get_adapter()
        _write_cold_path_trace(f"ADAPTER IS {adapter.__class__.__name__}")

        prompt = _get_extraction_prompt(content)

        # type: ignore[no-untyped-def]
        @retry(
            wait=wait_exponential(
                multiplier=1,
                min=config.retry_min_wait_sec,
                max=config.retry_max_wait_sec,
            ),
            stop=stop_after_attempt(config.retry_max_attempts),
        )
        async def _acomplete_with_retry():
            from mesa_memory.consolidation.loop import llm_circuit_breaker

            if llm_circuit_breaker.is_open:
                raise Exception("Circuit breaker is OPEN. Failing fast.")
            try:
                res = await adapter.acomplete(
                    prompt,
                    max_tokens=512,
                    temperature=0.0,
                )
                llm_circuit_breaker.record_success()
                return res
            except Exception:
                llm_circuit_breaker.record_failure()
                raise

        raw_response = await _acomplete_with_retry()

        # Type narrowing: acomplete() returns Union[str, BaseModel].
        # We never pass a schema, so the response is always str at runtime,
        # but mypy cannot infer that — narrow explicitly.
        if isinstance(raw_response, str):
            response_text = raw_response
        else:
            response_text = raw_response.model_dump_json()

        # Parse the JSON array from the LLM response
        triplets = _parse_llm_triplet_response(response_text)

        logger.debug(
            "LLM_TRIPLET_EXTRACT | lang=%s content_len=%d triplets=%d",
            config.extraction_lang,
            len(content),
            len(triplets),
        )
        return triplets

    except Exception as exc:
        logger.warning("LLM_TRIPLET_EXTRACT_ERROR | error=%s — returning empty", exc)
        if isinstance(exc, RetryError) or "Circuit breaker is OPEN" in str(exc):
            raise
        return []


# ---------------------------------------------------------------------------
# JSON sanitisation — standalone, no adapter dependency
# ---------------------------------------------------------------------------


def _sanitize_llm_json(raw: str) -> str:
    """Extract clean JSON from LLM output that may contain markdown fences,
    prose, trailing commas, or other common LLM output quirks.

    4-layer sanitisation pipeline:
        1. **Markdown fence extraction**: ``````json ... `````` → inner content.
        2. **Outermost JSON detection**: Find the first ``[`` or ``{`` and
           the last ``]`` or ``}`` to isolate the JSON structure.
        3. **Trailing comma repair**: Remove commas before ``]`` or ``}``.
        4. **Passthrough**: Return the original text if no JSON structure
           is detected (will fail at json.loads and be handled upstream).

    This is a standalone function with zero external dependencies.
    """
    import re

    text = raw.strip()

    # Layer 1: Extract from markdown code fences (```json ... ``` or ``` ... ```)
    fence_match = re.search(
        r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE
    )
    if fence_match:
        text = fence_match.group(1).strip()

    # Layer 2: Find the outermost JSON structure (array or object)
    arr_start = text.find("[")
    obj_start = text.find("{")

    if arr_start == -1 and obj_start == -1:
        return text  # No JSON structure — passthrough

    # Pick the earlier start delimiter
    if arr_start == -1:
        start_idx = obj_start
    elif obj_start == -1:
        start_idx = arr_start
    else:
        start_idx = min(arr_start, obj_start)

    arr_end = text.rfind("]")
    obj_end = text.rfind("}")

    if arr_end == -1 and obj_end == -1:
        return text  # No closing delimiter — passthrough

    # Pick the later end delimiter
    end_idx = max(arr_end, obj_end)

    if end_idx > start_idx:
        text = text[start_idx : end_idx + 1]

    # Layer 3: Repair trailing commas (e.g., [{"a": 1},] → [{"a": 1}])
    text = re.sub(r",\s*([}\]])", r"\1", text)

    return text


def _parse_llm_triplet_response(raw: str) -> list[dict[str, str]]:
    """Parse and validate the LLM JSON response into normalised triplets.

    Handles both key formats:
        - ``{subject, predicate, object, confidence}`` (Turkish/new prompt format)
        - ``{head, relation, tail}`` (REBEL/legacy prompt format)

    Both are normalised to the canonical ``{head, relation, tail, confidence}``
    output consumed by ``_commit_triplets``.

    Sanitisation pipeline:
        1. Markdown fence stripping + outermost JSON detection.
        2. Trailing comma repair.
        3. JSON parsing with graceful degradation.
        4. Per-entry validation — invalid entries silently dropped.

    Returns:
        List of ``{head, relation, tail, confidence}`` dicts.
    """
    cleaned = _sanitize_llm_json(raw)

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

        # Normalise both key formats to canonical {head, relation, tail}
        head = str(entry.get("head", entry.get("subject", ""))).strip()
        relation = str(entry.get("relation", entry.get("predicate", ""))).strip()
        tail = str(entry.get("tail", entry.get("object", ""))).strip()

        # Extract confidence — default to 1.0 only if the key is
        # entirely absent or unparseable (strict fallback policy).
        raw_conf = entry.get("confidence")
        if raw_conf is not None:
            try:
                confidence = max(0.0, min(1.0, float(raw_conf)))
            except (ValueError, TypeError):
                confidence = 1.0
        else:
            confidence = 1.0

        if head and relation and tail:
            triplets.append(
                {
                    "head": head,
                    "relation": relation,
                    "tail": tail,
                    "confidence": str(confidence),
                }
            )

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

    Enforces **atomic saga ordering** to minimise orphaned state:

        Stage 1: SQLite node INSERT (can ROLLBACK via transaction)
        Stage 2: LanceDB vector upsert (compensate via soft-delete)
        Stage 3: KuzuDB graph MERGE (LAST — no easy rollback)

    On any stage failure, compensating actions clean up earlier stages
    and the specific failing stage is logged with full ``exc_info``.

    Args:
        dao: Initialised MemoryDAO instance.
        agent_id: Agent scope for RLS enforcement.
        session_id: Session scope within the agent.
        content: Original content (used for node context).
        triplets: List of ``{head, relation, tail}`` dicts from REBEL.
        log_id: raw_logs primary key (used for node context tagging).
    """
    # Batch compute embeddings to prevent N+1 queries
    unique_entities = set()
    for triplet in triplets:
        head = triplet.get("head", "").strip()
        tail = triplet.get("tail", "").strip()
        if head:
            unique_entities.add(head[:512])
        if tail:
            unique_entities.add(tail[:512])

    unique_entities_list = list(unique_entities)
    if unique_entities_list:
        embeddings_list = await dao.vector_engine.compute_embedding_batch(
            unique_entities_list
        )
        embedding_map = {
            ent: emb for ent, emb in zip(unique_entities_list, embeddings_list)
        }
    else:
        embedding_map = {}

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

        # Phase 4.1: Epistemic uncertainty from extraction confidence.
        raw_conf = triplet.get("confidence")
        if raw_conf is not None:
            try:
                confidence = max(0.0, min(1.0, float(raw_conf)))
            except (ValueError, TypeError):
                confidence = 1.0
        else:
            confidence = 1.0
        epistemic_uncertainty = max(0.0, min(1.0, 1.0 - confidence))

        # --- Track saga stage for compensating rollback ---
        head_node_id: str | None = None
        tail_node_id: str | None = None
        tail_vector_ok = False

        try:
            # ==============================================================
            # STAGE 1: Compute embeddings (CPU — no side effects)
            # ==============================================================
            head_embedding = embedding_map.get(head[:512], [])
            tail_embedding = embedding_map.get(tail[:512], [])

            # ==============================================================
            # STAGE 2: SQLite INSERT (can rollback via DAO transaction)
            # ==============================================================
            head_node_id = await dao.insert_memory(
                agent_id,
                entity_name=head,
                content=f"[raw_log:{log_id}] {head}",
                embedding=head_embedding,
                node_type="ENTITY",
                session_id=session_id,
            )
            # insert_memory does dual-write (SQL+LanceDB)

            tail_node_id = await dao.insert_memory(
                agent_id,
                entity_name=tail,
                content=f"[raw_log:{log_id}] {tail}",
                embedding=tail_embedding,
                node_type="ENTITY",
                session_id=session_id,
            )
            tail_vector_ok = True

            # ==============================================================
            # STAGE 3: KuzuDB edge MERGE (LAST — hardest to rollback)
            # ==============================================================
            await dao.insert_edge(
                agent_id,
                source_id=head_node_id,
                target_id=tail_node_id,
                relation_type=relation or "RELATED_TO",
                weight=confidence,
                epistemic_uncertainty=epistemic_uncertainty,
            )

            logger.debug(
                "TRIPLET_COMMITTED | log_id=%d head=%s rel=%s tail=%s eu=%.3f",
                log_id,
                head,
                relation,
                tail,
                epistemic_uncertainty,
            )

        except Exception as exc:
            # --- Compensating rollback based on which stage failed ---
            failed_stage = "unknown"
            if head_node_id is None:
                failed_stage = "stage1_embedding_or_sqlite"
            elif not tail_vector_ok:
                failed_stage = "stage2_tail_insert"
                # Head was inserted — soft-delete to compensate
                try:
                    await dao.vector_engine.soft_delete(head_node_id, agent_id)
                except Exception as comp_exc:
                    logger.error(
                        "SAGA_COMPENSATE_FAILED | log_id=%d stage=head_softdelete error=%s",
                        log_id,
                        comp_exc,
                        exc_info=True,
                    )
            else:
                failed_stage = "stage3_kuzu_edge"
                # Both nodes inserted but edge failed — soft-delete both vectors
                for nid in (head_node_id, tail_node_id):
                    if nid:
                        try:
                            await dao.vector_engine.soft_delete(nid, agent_id)
                        except Exception as comp_exc:
                            logger.error(
                                "SAGA_COMPENSATE_FAILED | log_id=%d node_id=%s error=%s",
                                log_id,
                                nid,
                                comp_exc,
                                exc_info=True,
                            )

            logger.warning(
                "TRIPLET_COMMIT_FAILED | log_id=%d head=%s tail=%s "
                "failed_stage=%s error=%s",
                log_id,
                head,
                tail,
                failed_stage,
                exc,
                exc_info=True,
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

    Generates a real semantic embedding via ``VectorEngine.compute_embedding()``
    to ensure correct dimensionality for production models.

    Includes compensating saga rollback: if the dual-write
    (SQLite + LanceDB via ``insert_memory``) fails, any partially
    committed vector entry is soft-deleted to maintain consistency.

    Args:
        dao: Initialised MemoryDAO instance.
        agent_id: Agent scope for RLS enforcement.
        session_id: Session scope within the agent.
        content: Original content text.
        log_id: raw_logs primary key.
    """
    node_id: str | None = None

    try:
        try:
            embedding = await dao.vector_engine.compute_embedding(content[:512])
        except RuntimeError as exc:
            # The isolated model-disabled Compose profile must never silently
            # persist an all-zero vector or attempt a network model download.
            # A deterministic non-zero fallback keeps the raw-memory write
            # durable while FTS remains the primary retrieval signal.
            logger.warning(
                "RAW_MEMORY_USING_DETERMINISTIC_EMBEDDING | log_id=%d error=%s",
                log_id,
                exc,
            )
            embedding = _hash_embedding_sync(content[:512])

        node_id = await dao.insert_memory(
            agent_id,
            entity_name=content[:256],
            content=content,
            embedding=embedding,
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

    except Exception as exc:
        # --- Compensating rollback: soft-delete vector if node was written ---
        if node_id is not None:
            try:
                await dao.vector_engine.soft_delete(node_id, agent_id)
            except Exception as comp_exc:
                logger.error(
                    "SAGA_COMPENSATE_FAILED | log_id=%d "
                    "stage=raw_memory_softdelete node_id=%s error=%s",
                    log_id,
                    node_id,
                    comp_exc,
                    exc_info=True,
                )

        logger.warning(
            "RAW_MEMORY_COMMIT_FAILED | log_id=%d agent_id=%s error=%s",
            log_id,
            agent_id,
            exc,
            exc_info=True,
        )
        raise


async def process_session_finalization(
    agent_id: str,
    session_id: str,
    dao: MemoryDAO,
    consolidation_loop: ConsolidationLoop | None,
) -> str:
    """Run one fenced, restart-safe finalization attempt for an exact session."""
    worker_id = f"session-finalizer:{agent_id}"
    claim = await dao.claim_session_finalization(
        agent_id, session_id, worker_id=worker_id
    )
    if claim is None:
        current = await dao.get_session_finalization(agent_id, session_id)
        return current["state"] if current else "MISSING"
    if consolidation_loop is None:
        await dao.fail_session_finalization(
            agent_id,
            session_id,
            worker_id=worker_id,
            claim_token=claim["claim_token"],
            error_class="ConsolidationUnavailable",
        )
        current = await dao.get_session_finalization(agent_id, session_id)
        return current["state"] if current else "MISSING"
    try:
        for log_id in await dao.get_pending_session_raw_logs(agent_id, session_id):
            await process_cold_path(log_id, agent_id, dao, consolidation_loop)
        if await dao.complete_session_finalization(
            agent_id,
            session_id,
            worker_id=worker_id,
            claim_token=claim["claim_token"],
        ):
            return "COMPLETED"
        await dao.fail_session_finalization(
            agent_id,
            session_id,
            worker_id=worker_id,
            claim_token=claim["claim_token"],
            error_class="IncompleteSessionWork",
        )
        return "RETRY_PENDING"
    except Exception as exc:
        await dao.fail_session_finalization(
            agent_id,
            session_id,
            worker_id=worker_id,
            claim_token=claim["claim_token"],
            error_class=type(exc).__name__,
        )
        return "RETRY_PENDING"
