"""Consume durable V4 raw logs through validation and canonical extraction."""

from __future__ import annotations

import asyncio
import inspect
import re
from typing import Any

import structlog

from mesa_memory.config import config, configured_embedding_identity
from mesa_memory.consolidation.loop import ConsolidationLoop
from mesa_memory.consolidation.policy import DeterministicOnlyValidationPolicy
from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_storage.dao import MemoryDAO

logger = structlog.get_logger("MESA_ColdPath")

# ---------------------------------------------------------------------------
# Module-level singletons — initialised lazily on first cold-path call
# ---------------------------------------------------------------------------

MAX_CONCURRENT_WORKERS = asyncio.Semaphore(100)
MAX_TIER3_CONCURRENT = 3
_tier3_semaphore = asyncio.Semaphore(MAX_TIER3_CONCURRENT)
_CONTEXT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _safe_context_id(value: str) -> str:
    """Return only identifiers that are safe to propagate into log context."""
    return (
        value
        if value not in {"__unset__", "__system__"} and _CONTEXT_ID_RE.fullmatch(value)
        else "invalid"
    )


async def _await_optional_dao_call(
    dao: Any, method_name: str, *args: Any, **kwargs: Any
) -> Any:
    """Await real async DAO hooks while retaining lightweight test doubles.

    `MagicMock` manufactures arbitrary attributes, so `hasattr` is not a
    reliable capability check. A real DAO method (or an explicit AsyncMock)
    returns an awaitable; an unspecified compatibility mock is a no-op.
    """
    method = getattr(dao, method_name, None)
    if method is None:
        return None
    result = method(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return None


# ---------------------------------------------------------------------------
# Cold Path — main entry point
# ---------------------------------------------------------------------------


async def process_cold_path(
    log_id: int,
    agent_id: str,
    dao: MemoryDAO,
    consolidation_loop: ConsolidationLoop | None = None,
    *,
    model_processing_enabled: bool = True,
    require_tier3_validation: bool = False,
    retry_on_failure: bool = False,
) -> None:
    """Bind one safe correlation context around a cold-path job.

    Model-disabled records retain the deterministic V4 lifecycle. Canonical
    extraction and validation use the supplied consolidation loop.
    """
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        worker_id="cold-path",
        operation_id=str(log_id),
        agent_id=_safe_context_id(agent_id),
    )
    try:
        await _process_cold_path_impl(
            log_id,
            agent_id,
            dao,
            consolidation_loop,
            model_processing_enabled=model_processing_enabled,
            require_tier3_validation=require_tier3_validation,
            retry_on_failure=retry_on_failure,
        )
    finally:
        structlog.contextvars.clear_contextvars()


async def _process_cold_path_impl(
    log_id: int,
    agent_id: str,
    dao: MemoryDAO,
    consolidation_loop: ConsolidationLoop | None = None,
    *,
    model_processing_enabled: bool = True,
    require_tier3_validation: bool = False,
    retry_on_failure: bool = False,
) -> None:
    """Claim a durable V4 record, validate it and persist its processing outcome."""
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

    try:
        async with MAX_CONCURRENT_WORKERS:
            # ==============================================================
            # 1. RETRIEVE PAYLOAD + STATUS GUARD
            # ==============================================================
            if type(dao) is MemoryDAO:
                claim = await dao.claim_raw_log(agent_id, log_id, worker_id="cold-path")
                raw_log = claim
            else:
                raw_log = await dao.get_raw_log(agent_id, log_id)

            if raw_log is None:
                logger.warning("COLD_PATH_SKIP | log_id=%d reason=not_found", log_id)
                return

            if claim is None and raw_log["status"] != "DEFERRED":
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
            job_context: dict[str, Any] = {"session_id": _safe_context_id(session_id)}
            attempt = raw_log.get("attempt_count")
            if isinstance(attempt, int):
                job_context["attempt"] = attempt
            structlog.contextvars.bind_contextvars(**job_context)

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

            admitted_mode = payload.get("validation_mode")
            if admitted_mode is None and isinstance(metadata, dict):
                admitted_mode = metadata.get("_mesa_validation_mode")
            effective_validation_mode = (
                admitted_mode
                if admitted_mode is not None
                else config.effective_tier3_mode(model_enabled=model_processing_enabled)
            )

            is_v4_pipeline = (
                admitted_mode is not None
                or bool(payload.get("document_id"))
            )

            if is_v4_pipeline:
                embedding_identity = configured_embedding_identity()
                candidate = MemoryCandidate.from_raw_log(
                    raw_log_id=log_id,
                    agent_id=payload_agent_id,
                    session_id=session_id,
                    content_payload=content,
                    metadata=metadata,
                    # V4 admits its canonical candidate before dispatch.  The
                    # consumer must rebuild the exact same identity, rather
                    # than falling back to legacy agent-derived provenance.
                    tenant_id=payload.get("tenant_id"),
                    workspace_id=payload.get("workspace_id"),
                    dataset_id=payload.get("dataset_id"),
                    document_id=payload.get("document_id"),
                    revision_id=payload.get("revision_id"),
                    chunk_id=payload.get("chunk_id"),
                    source_ref=payload.get("source_ref"),
                    evidence_span=str(payload.get("evidence_span", "")),
                    embedding_provider=embedding_identity.provider,
                    embedding_model=embedding_identity.model,
                    embedding_version=embedding_identity.version,
                    embedding_dimension=embedding_identity.dimension,
                    validation_mode=effective_validation_mode,
                )
                candidate_record = candidate.as_consolidation_record()
                # v4 callers persist the canonical hand-off before validation;
                # lightweight legacy mocks intentionally remain supported.
                if not bool(payload.get("chunk_id")):
                    await _await_optional_dao_call(
                        dao, "record_mutation", candidate_record, raw_log_id=log_id
                    )

                mutation = await _await_optional_dao_call(
                    dao, "get_mutation", payload_agent_id, candidate.mutation_id
                )
                mutation_state = (
                    mutation.get("state") if isinstance(mutation, dict) else None
                )
                if mutation_state in {
                    "VALIDATED",
                    "PENDING",
                    "SQL_APPLIED",
                    "VECTOR_APPLIED",
                    "GRAPH_APPLIED",
                    "COMMITTED",
                }:
                    await _transition("processed", target_agent_id=payload_agent_id)
                    return
                if mutation_state == "REJECTED":
                    await _transition("rejected", target_agent_id=payload_agent_id)
                    return
                if mutation_state in {
                    "DEAD_LETTER",
                    "BLOCKED",
                    "ROLLING_BACK",
                    "ROLLED_BACK",
                    "PURGING",
                    "PURGED",
                    "CANCELLED",
                }:
                    await _transition("failed", target_agent_id=payload_agent_id)
                    return
                if effective_validation_mode > 0 and (
                    not model_processing_enabled or consolidation_loop is None
                ):
                    raise RuntimeError(
                        "LLM validation requires an active model runtime and consolidation loop"
                    )

                if consolidation_loop is not None:
                    async with _tier3_semaphore:
                        outcome = await consolidation_loop.run_batch([candidate_record])
                else:
                    # Deterministic Mode 0 path without active loop
                    mode_zero_audit = (
                        await DeterministicOnlyValidationPolicy().validate_with_audit(
                            candidate_record
                        )
                    )
                    candidate_record["_mesa_tier3_audit"] = mode_zero_audit
                    # Mode 0 bypasses ConsolidationLoop, which normally persists
                    # extraction and advances RECEIVED -> EXTRACTED.  Preserve the
                    # same durable lifecycle even when model extraction is disabled.
                    persisted = await _await_optional_dao_call(
                        dao,
                        "record_mutation_extraction",
                        payload_agent_id,
                        candidate.mutation_id,
                        [],
                    )
                    if persisted is False:
                        await _transition("DEFERRED", target_agent_id=payload_agent_id)
                        return
                    outcome = {
                        "accepted": [candidate.candidate_id],
                        "rejected": [],
                        "deferred": [],
                    }

                tier3_audit = candidate_record.get("_mesa_tier3_audit")
                event_detail: dict[str, Any] | None = None
                if isinstance(tier3_audit, dict):
                    event_detail = {"tier3": tier3_audit}
                    await _await_optional_dao_call(
                        dao,
                        "record_mutation_tier3_audit",
                        payload_agent_id,
                        candidate.mutation_id,
                        tier3_audit,
                    )
                if candidate.candidate_id in outcome.get("accepted", []):
                    validated = await _await_optional_dao_call(
                        dao,
                        "set_mutation_state",
                        payload_agent_id,
                        candidate.mutation_id,
                        "VALIDATED",
                        event_detail=event_detail,
                    )
                    if validated is False:
                        await _transition("DEFERRED", target_agent_id=payload_agent_id)
                        return
                    await _transition("processed", target_agent_id=payload_agent_id)
                    logger.info(
                        "COLD_PATH_VALIDATED_AND_PROJECTED | log_id=%d candidate_id=%s mode=%d",
                        log_id,
                        candidate.candidate_id,
                        effective_validation_mode,
                    )
                    return
                if candidate.candidate_id in outcome.get("rejected", []):
                    await _await_optional_dao_call(
                        dao,
                        "set_mutation_state",
                        payload_agent_id,
                        candidate.mutation_id,
                        "REJECTED",
                        failure_class="Tier3Rejected",
                        event_detail=event_detail,
                    )
                    await _transition(
                        "rejected",
                        error_reason="tier3_validation_rejected",
                        target_agent_id=payload_agent_id,
                    )
                    return
                await _await_optional_dao_call(
                    dao,
                    "set_mutation_state",
                    payload_agent_id,
                    candidate.mutation_id,
                    "RETRY_PENDING",
                    failure_class="Tier3Unavailable",
                    event_detail=event_detail,
                )
                await _transition("DEFERRED", target_agent_id=payload_agent_id)
                return

            await _transition(
                "rejected",
                error_reason="legacy_payload_unsupported",
                target_agent_id=payload_agent_id,
            )
            return

    except Exception as exc:
        # ==============================================================
        # SAFETY NET — catch ALL exceptions, update status, log trace
        # ==============================================================
        error_type = type(exc).__name__
        error_msg = f"{error_type}: {exc}"

        # Truncate error reason to prevent oversized status fields
        truncated_reason = error_msg[:500]

        try:
            if retry_on_failure:
                await _transition("DEFERRED", target_agent_id=agent_id)
            else:
                await _transition(
                    "failed", error_reason=truncated_reason, target_agent_id=agent_id
                )
        except Exception as status_exc:
            # Even the status update failed — log but never raise
            logger.critical(
                "COLD_PATH_STATUS_UPDATE_FAILED",
                log_id=log_id,
                original_exception_type=error_type,
                status_exception_type=type(status_exc).__name__,
            )

        logger.error(
            "COLD_PATH_FAILED",
            log_id=log_id,
            exception_type=error_type,
            exc_info=True,
        )
        # CRITICAL: Do NOT re-raise — protect the BG task pool


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
    try:
        pending_logs = await dao.get_pending_session_raw_logs(agent_id, session_id)
        if pending_logs:
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
            for log_id in pending_logs:
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
