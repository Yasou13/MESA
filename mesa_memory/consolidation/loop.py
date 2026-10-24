"""Validate canonical V4 mutations and persist extracted facts through MemoryDAO."""

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from tenacity import RetryError, retry, stop_after_attempt, wait_exponential

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import config
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
from mesa_memory.consolidation.policy import (
    DeterministicOnlyValidationPolicy,
    DualLLMValidationPolicy,
    SingleLLMValidationPolicy,
    ValidationPolicy,
    compose_validation_policy,
)
from mesa_memory.consolidation.router import AdaptiveRouter
from mesa_memory.consolidation.schemas import ExtractedTriplet
from mesa_memory.consolidation.validator import Tier3ValidationError, Tier3Validator
from mesa_memory.extraction.service import (
    FactCandidate,
    FactExtractionError,
    FactExtractionService,
)
from mesa_memory.observability.metrics import ObservabilityLayer
from mesa_storage.dao import MemoryDAO

logger = logging.getLogger("MESA_Consolidation")


# ---------------------------------------------------------------------------
# Persistent Queue
# ---------------------------------------------------------------------------
class PersistentQueue:
    """Durable JSONL DLQ with file-locked claim/ack/nack semantics.

    This retains the configured JSONL representation but records ownership and
    retry metadata in each entry.  It is deliberately not a second raw-log
    claim implementation: raw logs keep the SQLite WAVE-003 protocol.
    """

    _MAX_QUEUE_BYTES = 100 * 1024 * 1024
    _MAX_ATTEMPTS = 4

    def __init__(
        self,
        filepath: str,
        *,
        trusted_root: str | None = None,
        require_completion_receipt: bool = False,
        _test_crash_hook: Callable[[str], None] | None = None,
    ):
        self.filepath = filepath
        self.receipt_filepath = filepath + ".receipts.jsonl"
        self._trusted_root_input = Path(trusted_root) if trusted_root else None
        self._trusted_root = (
            self._trusted_root_input.resolve(strict=False)
            if self._trusted_root_input
            else None
        )
        self._require_completion_receipt = require_completion_receipt
        self._file_lock = threading.Lock()
        # This hook has no environment/configuration path and is available only
        # to an explicit in-process test harness; production callers cannot
        # activate crash injection accidentally.
        self._test_crash_hook = _test_crash_hook
        self._validate_path_policy()
        os.makedirs(os.path.dirname(self.filepath), mode=0o700, exist_ok=True)

    def _validate_path_policy(self) -> None:
        """Fail closed for configured queue paths; legacy callers are not upgraded silently."""
        if self._trusted_root is None:
            return
        root = self._trusted_root
        repo = Path.cwd().resolve()
        home = Path.home().resolve()
        raw = Path(self.filepath)
        if not raw.is_absolute() or not str(raw) or ".." in raw.parts:
            raise ValueError("queue path must be an absolute non-traversing path")
        if (
            root in {Path("/"), home, repo}
            or self._trusted_root_input is None
            or self._trusted_root_input.is_symlink()
        ):
            raise ValueError("trusted queue root is forbidden or symlinked")
        target = raw.resolve(strict=False)
        receipt_target = Path(self.receipt_filepath).resolve(strict=False)
        try:
            target.relative_to(root)
            receipt_target.relative_to(root)
        except ValueError as exc:
            raise ValueError("queue path escapes trusted root") from exc
        for parent in (root, *target.parents):
            if parent == root.parent:
                break
            if parent.exists() and parent.is_symlink():
                raise ValueError("queue path has a symlinked parent")
        if raw.exists() and raw.is_symlink():
            raise ValueError("queue target must not be a symlink")
        receipt_path = Path(self.receipt_filepath)
        if receipt_path.exists() and receipt_path.is_symlink():
            raise ValueError("queue receipt target must not be a symlink")

    def _read_completion_receipts(self) -> dict[str, dict]:
        self._validate_path_policy()
        receipts: dict[str, dict] = {}
        try:
            with open(self.receipt_filepath, "r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    receipt = json.loads(line)
                    queue_id = receipt.get("queue_record_id")
                    if queue_id:
                        receipts[queue_id] = receipt
        except FileNotFoundError:
            pass
        return receipts

    def _write_completion_receipt_locked(
        self, claim: dict, *, worker_id: str, outcome: str
    ) -> dict:
        receipts = self._read_completion_receipts()
        queue_id = claim["queue_id"]
        existing = receipts.get(queue_id)
        if existing is not None:
            return existing
        receipt = {
            "receipt_id": os.urandom(16).hex(),
            "queue_record_id": queue_id,
            "dispatch_id": claim.get("dispatch_id", queue_id),
            "mutation_id": claim.get("mutation_id")
            or claim.get("idempotency_key")
            or queue_id,
            "idempotency_key": claim.get("idempotency_key") or f"dlq:{queue_id}",
            "tenant_id": claim.get("tenant_id") or claim.get("agent_id"),
            "agent_id": claim.get("agent_id"),
            "worker_id": worker_id,
            "claim_token": claim["claim_token"],
            "attempt_number": int(claim.get("attempt_count", 0)),
            "outcome": outcome,
            "side_effect_verification": True,
            "completed_at": time.time(),
            "receipt_version": 1,
            "error_class": None,
        }
        with open(self.receipt_filepath, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(receipt, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        directory_fd = os.open(
            os.path.dirname(os.path.abspath(self.receipt_filepath)), os.O_RDONLY
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        self._inject_test_crash("after_receipt_fsync_before_ack")
        return receipt

    # type: ignore[arg-type]
    def _inject_test_crash(self, point: str) -> None:
        if self._test_crash_hook is not None:
            self._test_crash_hook(point)

    @staticmethod
    def _now() -> float:
        return time.time()

    import typing

    @staticmethod
    def _expired(value: typing.Any, now: float) -> bool:
        try:
            return float(value) <= now
        except (TypeError, ValueError):
            return True

    def _normalize(self, item: dict) -> dict:
        record = dict(item)
        record.setdefault("queue_id", os.urandom(16).hex())
        record.setdefault("state", "PENDING")
        record.setdefault("attempt_count", 0)
        record.setdefault("claimed_by", None)
        record.setdefault("claim_token", None)
        record.setdefault("lease_expires_at", None)
        # Never persist raw exception text in the DLQ file.
        record.pop("error", None)
        record.setdefault("error_summary", "failure recorded")
        record.setdefault("last_error_type", None)
        return record

    def _quarantine_malformed_line(
        self, line_number: int, raw_line: bytes, error: Exception
    ) -> None:
        """Persist forensic metadata without retaining a potentially sensitive payload."""
        event = {
            "line_number": line_number,
            "byte_length": len(raw_line),
            "sha256": hashlib.sha256(raw_line).hexdigest(),
            "error_type": type(error).__name__,
        }
        quarantine = self.filepath + ".malformed.jsonl"
        with open(quarantine, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    import typing

    def _locked_records(self) -> tuple[typing.IO[typing.Any], list[dict]]:
        import fcntl

        self._validate_path_policy()
        lock_handle = open(self.filepath + ".lock", "a+")
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            records: list[dict] = []
            malformed = False
            try:
                with open(self.filepath, "rb") as handle:
                    for line_number, raw_line in enumerate(handle, start=1):
                        if not raw_line.strip():
                            continue
                        try:
                            decoded = raw_line.decode("utf-8")
                            record = json.loads(decoded)
                            if not isinstance(record, dict):
                                raise ValueError("DLQ record must be a JSON object")
                            records.append(self._normalize(record))
                        except (
                            UnicodeDecodeError,
                            json.JSONDecodeError,
                            ValueError,
                        ) as exc:
                            self._quarantine_malformed_line(line_number, raw_line, exc)
                            malformed = True
            except FileNotFoundError:
                pass
            if malformed:
                # Valid records remain available; malformed input is durably
                # quarantined and removed from the live replay file once.
                self._rewrite_locked(records)
        except Exception:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()
            raise
        return lock_handle, records

    def _rewrite_locked(self, records: list[dict]) -> None:
        self._validate_path_policy()
        temporary = self.filepath + ".tmp"
        self._inject_test_crash("before_serialization")
        serialized = [json.dumps(record, sort_keys=True) + "\n" for record in records]
        self._inject_test_crash("after_serialization_before_file_open")
        with open(temporary, "w", encoding="utf-8") as handle:
            self._inject_test_crash("after_file_open_before_write")
            for line in serialized:
                if self._test_crash_hook is not None:  # type: ignore[attr-defined]
                    self._inject_test_crash("before_write")  # type: ignore[attr-defined]
                handle.write(line)
                self._inject_test_crash("after_write_before_flush")
            handle.flush()
            self._inject_test_crash("after_flush_before_fsync")
            os.fsync(handle.fileno())
            self._inject_test_crash("after_fsync_before_close")
        self._inject_test_crash("after_close_before_rename")
        os.replace(temporary, self.filepath)
        self._inject_test_crash("after_rename_before_directory_fsync")
        directory_fd = os.open(
            os.path.dirname(os.path.abspath(self.filepath)), os.O_RDONLY
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        self._inject_test_crash("after_directory_fsync")

    import typing

    @staticmethod
    def _unlock(lock_handle: typing.IO[typing.Any]) -> None:
        import fcntl

        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()

    async def aappend(self, item: dict) -> None:
        await asyncio.get_running_loop().run_in_executor(None, self.append, item)

    def append(self, item: dict) -> None:
        with self._file_lock:
            lock_handle, records = self._locked_records()
            try:
                if (
                    os.path.exists(self.filepath)
                    and os.path.getsize(self.filepath) > self._MAX_QUEUE_BYTES
                ):
                    raise RuntimeError("DLQ capacity limit reached")
                record = self._normalize(item)
                if any(
                    existing["queue_id"] == record["queue_id"] for existing in records
                ):
                    raise ValueError("duplicate DLQ queue_id")
                records.append(record)
                self._rewrite_locked(records)
            finally:
                self._unlock(lock_handle)

    async def aclaim(
        self, *, worker_id: str, limit: int = 10, lease_seconds: int = 300
    ) -> list[dict]:
        if not worker_id or not 1 <= limit <= 1000 or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid DLQ claim bounds")
        return await asyncio.get_running_loop().run_in_executor(
            None, self._claim, worker_id, limit, lease_seconds
        )

    def _claim(self, worker_id: str, limit: int, lease_seconds: int) -> list[dict]:
        now = self._now()
        claimed: list[dict] = []
        with self._file_lock:
            lock_handle, records = self._locked_records()
            try:
                for record in records:
                    eligible = record["state"] == "PENDING" or (
                        record["state"] == "PROCESSING"
                        and self._expired(record.get("lease_expires_at"), now)
                    )
                    if not eligible or len(claimed) >= limit:
                        continue
                    record["state"] = "PROCESSING"
                    record["claimed_by"] = worker_id
                    record["claim_token"] = os.urandom(16).hex()
                    record["lease_expires_at"] = now + lease_seconds
                    record["attempt_count"] = int(record.get("attempt_count", 0)) + 1
                    claimed.append(dict(record))
                self._rewrite_locked(records)
            finally:
                self._unlock(lock_handle)
        return claimed

    async def aack(self, items: list[dict], *, worker_id: str) -> bool:
        return await asyncio.get_running_loop().run_in_executor(
            None, self._ack, items, worker_id
        )

    def _ack(self, items: list[dict], worker_id: str) -> bool:
        wanted = {(item.get("queue_id"), item.get("claim_token")) for item in items}
        if not wanted:
            return True
        with self._file_lock:
            lock_handle, records = self._locked_records()
            try:
                matched = {
                    (record.get("queue_id"), record.get("claim_token"))
                    for record in records
                    if record.get("state") == "PROCESSING"
                    and record.get("claimed_by") == worker_id
                }
                if not wanted.issubset(matched):
                    return False
                if self._require_completion_receipt:
                    receipts = self._read_completion_receipts()
                    if any(queue_id not in receipts for queue_id, _ in wanted):
                        return False
                records = [
                    record
                    for record in records
                    if (record.get("queue_id"), record.get("claim_token")) not in wanted
                ]
                self._rewrite_locked(records)
                return True
            finally:
                self._unlock(lock_handle)

    async def acomplete(
        self,
        item: dict,
        *,
        worker_id: str,
        outcome: str = "SUCCEEDED",
        side_effect_verified: bool,
    ) -> bool:
        """Durably receipt one verified side effect before fenced ACK."""
        return await asyncio.get_running_loop().run_in_executor(
            None, self._complete, item, worker_id, outcome, side_effect_verified
        )

    def _complete(
        self, item: dict, worker_id: str, outcome: str, side_effect_verified: bool
    ) -> bool:
        if not side_effect_verified:
            return False
        identity = (item.get("queue_id"), item.get("claim_token"))
        with self._file_lock:
            lock_handle, records = self._locked_records()
            try:
                match = next(
                    (
                        record
                        for record in records
                        if (record.get("queue_id"), record.get("claim_token"))
                        == identity
                        and record.get("state") == "PROCESSING"
                        and record.get("claimed_by") == worker_id
                    ),
                    None,
                )
                if match is None:
                    return False
                self._write_completion_receipt_locked(
                    match, worker_id=worker_id, outcome=outcome
                )
                records = [record for record in records if record is not match]
                self._rewrite_locked(records)
                return True
            finally:
                self._unlock(lock_handle)

    async def acompletion_receipt(self, queue_id: str) -> dict | None:
        return await asyncio.get_running_loop().run_in_executor(
            None, lambda: self._read_completion_receipts().get(queue_id)
        )

    async def areconcile_receipted_claim(self, item: dict, *, worker_id: str) -> bool:
        """After restart, ACK a current claim whose prior durable receipt is valid."""
        receipt = await self.acompletion_receipt(str(item.get("queue_id", "")))
        if receipt is None or receipt.get("side_effect_verification") is not True:
            return False
        return await self.aack([item], worker_id=worker_id)

    async def anack(
        self, items: list[dict], *, worker_id: str, error_type: str
    ) -> bool:
        return await asyncio.get_running_loop().run_in_executor(
            None, self._nack, items, worker_id, error_type
        )

    def _nack(self, items: list[dict], worker_id: str, error_type: str) -> bool:
        wanted = {(item.get("queue_id"), item.get("claim_token")) for item in items}
        if not wanted:
            return True
        with self._file_lock:
            lock_handle, records = self._locked_records()
            try:
                seen: set[tuple[object, object]] = set()
                for record in records:
                    identity = (record.get("queue_id"), record.get("claim_token"))
                    if identity not in wanted:
                        continue
                    if (
                        record.get("state") != "PROCESSING"
                        or record.get("claimed_by") != worker_id
                    ):
                        return False
                    record["last_error_type"] = error_type[:80]
                    record["claimed_by"] = None  # type: ignore[no-untyped-def]
                    record["claim_token"] = None
                    record["lease_expires_at"] = None
                    record["state"] = (
                        "BLOCKED"
                        if int(record["attempt_count"]) >= self._MAX_ATTEMPTS
                        else "PENDING"
                    )
                    seen.add(identity)  # type: ignore[no-untyped-def]
                if seen != wanted:
                    return False
                self._rewrite_locked(records)
                return True
            finally:
                self._unlock(lock_handle)

    async def aremove_items(self, items: list[dict]) -> None:
        """Compatibility alias: only an owned claim may be acknowledged."""
        if not items:
            return
        worker_id = items[0].get("claimed_by")
        if not worker_id or not await self.aack(items, worker_id=worker_id):
            raise RuntimeError("DLQ compatibility removal requires an owned claim")

    async def alen(self) -> int:
        def _len() -> int:
            with self._file_lock:
                lock_handle, records = self._locked_records()
                try:
                    return len(records)
                finally:
                    self._unlock(lock_handle)

        return await asyncio.get_running_loop().run_in_executor(None, _len)

    async def agetitem(self, index: int) -> dict:
        def _getitem() -> dict:
            with self._file_lock:
                lock_handle, records = self._locked_records()
                try:
                    return records[index]
                finally:
                    self._unlock(lock_handle)

        return await asyncio.get_running_loop().run_in_executor(None, _getitem)


# ---------------------------------------------------------------------------
# Resilience: Circuit Breaker
# ---------------------------------------------------------------------------
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 10, cooldown_period: float = 60.0):
        self.failure_threshold = failure_threshold
        self.cooldown_period = cooldown_period
        self.failures = 0
        self.last_failure_time = 0.0

    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.failure_threshold:
            logger.critical(
                "CIRCUIT BREAKER OPENED: %d consecutive failures.", self.failures
            )

    def record_success(self):
        if self.failures >= self.failure_threshold:
            logger.info("CIRCUIT BREAKER CLOSED: Connection recovered.")
        self.failures = 0

    @property
    def is_open(self) -> bool:
        if self.failures >= self.failure_threshold:
            if time.time() - self.last_failure_time < self.cooldown_period:
                return True
            else:
                return False
        return False


# Global circuit breaker instance
llm_circuit_breaker = CircuitBreaker(
    failure_threshold=config.circuit_breaker_threshold,
    cooldown_period=config.circuit_breaker_cooldown_sec,
)


# ---------------------------------------------------------------------------
# ConsolidationLoop — Pure Orchestrator (v0.3.1 DAO-wired)
# ---------------------------------------------------------------------------


class ConsolidationLoop:
    """Orchestrates batch processing of raw log records through the
    consolidation pipeline.

    All I/O flows through ``MemoryDAO``'s agent-scoped methods with
    RLS enforcement.  ``MemoryDAO`` is the single source of truth
    for all read/write operations.

    Delegates to:
    - ``FactExtractionService``: canonical fact extraction.
    - ``ValidationPolicy``: selected validation gate for deferred records.
    - ``BatchResponseParser``: Response parsing and recovery.  # type: ignore[no-untyped-def]

    Retains ownership of:
    - Batch queue management and lifecycle (start/stop).  # type: ignore[no-untyped-def]
    - Validation-policy gating.
    - Observability logging.
    """

    def __init__(
        self,
        dao: MemoryDAO,
        embedder: BaseUniversalLLMAdapter,
        llm_a: BaseUniversalLLMAdapter | None = None,
        llm_b: BaseUniversalLLMAdapter | None = None,
        obs_layer: ObservabilityLayer | None = None,
        agent_id: str = "mesa_consolidation_system",
        queue_root: str | Path | None = None,
        *,
        validation_policy: ValidationPolicy | None = None,
        extraction_llm: BaseUniversalLLMAdapter | None = None,
    ):
        self.dao = dao
        self.embedder = embedder
        self.llm_a = llm_a
        self.llm_b = llm_b
        self.obs_layer = obs_layer or ObservabilityLayer()
        self._agent_id = agent_id
        self._running = False

        # Persistent queues — paths sourced from central config (P7 fix)
        if queue_root is None:
            _hr_path = Path(config.human_review_queue_path)
            _dl_path = Path(config.dead_letter_queue_path)  # type: ignore[no-untyped-def]
            queue_trusted_root = config.storage_path
        else:
            isolated_queue_root = Path(queue_root)
            _hr_path = isolated_queue_root / "human-review.jsonl"
            _dl_path = isolated_queue_root / "dead-letter.jsonl"
            queue_trusted_root = str(isolated_queue_root)
        _hr_path.parent.mkdir(parents=True, exist_ok=True)
        _dl_path.parent.mkdir(parents=True, exist_ok=True)
        self.human_review_queue = PersistentQueue(
            str(_hr_path), trusted_root=queue_trusted_root
        )
        self.dead_letter_queue = PersistentQueue(
            str(_dl_path),
            trusted_root=queue_trusted_root,
            require_completion_receipt=True,
        )

        # Concurrency Control: Bound concurrent LLM API calls to prevent 429 Too Many Requests  # type: ignore[no-untyped-def]
        self._llm_semaphore = asyncio.Semaphore(5)

        # Validation Policy Resolution
        if validation_policy is not None:
            self.validation_policy = validation_policy
        elif llm_a is not None and llm_b is not None:
            self.validation_policy = DualLLMValidationPolicy(
                Tier3Validator(llm_a, llm_b)
            )
        elif llm_a is not None:
            self.validation_policy = SingleLLMValidationPolicy(llm_a)
        else:
            self.validation_policy = compose_validation_policy(
                config.effective_tier3_mode(model_enabled=True)
            )

        # Backward compatibility validator handle
        self.validator = (
            self.validation_policy.validator
            if isinstance(self.validation_policy, DualLLMValidationPolicy)
            else self.validation_policy
        )

        # Delegate modules: Extraction and Routing
        # An explicitly composed policy must not supply extraction adapters.
        # ``llm_a``/``llm_b`` remain an old positional compatibility path only
        # when no policy was injected by the runtime composition boundary.
        ext_a = extraction_llm
        if ext_a is None and validation_policy is None and llm_a is not None:
            ext_a = llm_a
        if ext_a is None:
            ext_a = embedder

        self.fact_extraction_service = FactExtractionService(
            llm=ext_a,
            extraction_lang=config.extraction_lang,
        )
        self.router = AdaptiveRouter(
            dao=dao,
            small_llm=ext_a or embedder,
            validation_policy=self.validation_policy,
            obs_layer=self.obs_layer,
        )
    # Expose rebel_extractor for backward compatibility

    async def start(self):
        """Main consolidation loop — polls DAO for unconsolidated records."""
        self._running = True
        while self._running:
            try:
                # Read from the unconsolidated hot-path via MemoryDAO
                records = await self.dao.get_memories(
                    self._agent_id,
                    include_consolidated=False,
                    limit=config.consolidation_batch_size,
                )
                if records:  # type: ignore[no-untyped-def]
                    await self.run_batch(records)
            except asyncio.CancelledError:
                logger.info("Consolidation loop cancelled, shutting down.")
                break
            except Exception as exc:
                logger.error(
                    "CONSOLIDATION_LOOP_ERROR | error=%s",
                    exc,
                    exc_info=True,
                )
            await asyncio.sleep(config.consolidation_idle_timeout)

    async def stop(self):
        self._running = False

    # -------------------------------------------------------------------
    # Backward-compatible delegation wrappers  # type: ignore[no-untyped-def]
    # -------------------------------------------------------------------


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


    # -------------------------------------------------------------------
    # Extraction with retry and concurrency control
    # -------------------------------------------------------------------


    # -------------------------------------------------------------------
    # Core batch orchestrator
    # -------------------------------------------------------------------

    async def run_batch(
        self, batch: Optional[list[dict]] = None
    ) -> dict[str, list[str]]:
        """Validate records and persist canonical V4 fact extraction."""
        if batch is None:
            batch = await self.dao.get_memories(
                self._agent_id,
                include_consolidated=False,
                limit=config.consolidation_batch_size,
            )

        outcome: dict[str, list[str]] = {"accepted": [], "rejected": [], "deferred": []}
        if not batch:
            return outcome

        # --- Phase 1: selected validation-policy gate ---
        ready_batch = []
        for record in batch:
            if (
                record.get("tier3_deferred")
                or record.get("validation_mode") is not None
            ):
                if _requires_provenance_dual_review(record):
                    record["_mesa_force_dual_llm"] = True
                try:
                    is_valid = await self._validate_with_timeout(record)
                except RetryError as exc:
                    # Infrastructure error (retries exhausted) — do NOT treat as cognitive DISCARD
                    logger.error(
                        "Tier-3 validation retries exhausted for %s (API down): %s",
                        record.get("cmb_id", record.get("id", "?")),
                        exc,
                    )
                    await self.dead_letter_queue.aappend(
                        {
                            "cmb_id": record.get("cmb_id", record.get("id", "")),
                            "agent_id": record.get("agent_id", self._agent_id),
                            "error": str(exc),
                        }
                    )
                    outcome["deferred"].append(
                        str(record.get("cmb_id", record.get("id", "")))
                    )
                    continue
                except Tier3ValidationError as exc:
                    # Infrastructure error — do NOT treat as cognitive DISCARD
                    logger.error(
                        "Tier-3 validation error for %s: %s",
                        record.get("cmb_id", record.get("id", "?")),
                        exc,
                    )
                    await self.dead_letter_queue.aappend(
                        {
                            "cmb_id": record.get("cmb_id", record.get("id", "")),
                            "agent_id": record.get("agent_id", self._agent_id),
                            "error": str(exc),
                        }
                    )
                    outcome["deferred"].append(
                        str(record.get("cmb_id", record.get("id", "")))
                    )
                    continue
                except (asyncio.TimeoutError, Exception) as exc:
                    # LLM timeout or unexpected error — dead-letter, don't crash
                    logger.error(
                        "Tier-3 validation unexpected error for %s: %s",
                        record.get("cmb_id", record.get("id", "?")),
                        exc,
                        exc_info=True,
                    )
                    await self.dead_letter_queue.aappend(
                        {
                            "cmb_id": record.get("cmb_id", record.get("id", "")),
                            "agent_id": record.get("agent_id", self._agent_id),
                            "error": f"unexpected: {exc}",
                        }
                    )
                    outcome["deferred"].append(
                        str(record.get("cmb_id", record.get("id", "")))
                    )
                    continue

                is_pass = False
                tier3_audit: dict[str, Any] | None = None
                if isinstance(is_valid, dict):
                    if "tier3_audit" in is_valid:
                        tier3_audit = is_valid.get("tier3_audit")
                        decision_val = is_valid.get("decision")
                        if decision_val is None and is_valid.get("route") == "dual_llm":
                            tier3_audit = await self.validator.validate_with_audit(
                                record
                            )
                            is_pass = bool(tier3_audit["accepted"])
                        elif decision_val is not None:
                            is_pass = decision_val in (True, "STORE", "ADMIT")
                        elif (
                            isinstance(tier3_audit, dict) and "accepted" in tier3_audit
                        ):
                            is_pass = bool(tier3_audit["accepted"])
                    elif "accepted" in is_valid:
                        tier3_audit = is_valid
                        is_pass = bool(is_valid["accepted"])
                    else:
                        decision_val = is_valid.get("decision")
                        is_pass = decision_val in (True, "STORE", "ADMIT")
                else:
                    is_pass = bool(is_valid)

                # The worker that owns the V4 mutation later persists this
                # bounded receipt with its pipeline transition.  Keep it on
                # the in-memory canonical record only; no raw LLM response or
                # prompt is ever attached here.
                if tier3_audit is not None:
                    record["_mesa_tier3_audit"] = tier3_audit

                if is_pass:
                    self.obs_layer.log_valence_decision(
                        tier=3,
                        decision="ADMIT",
                        justification="Selected validation policy satisfied in consolidation loop",
                        cost={"token_count": 0, "latency_ms": 0.0},
                    )
                    ready_batch.append(record)
                    outcome["accepted"].append(
                        str(record.get("cmb_id", record.get("id", "")))
                    )
                else:
                    self.obs_layer.log_valence_decision(
                        tier=3,
                        decision="DISCARD",
                        justification="Selected validation policy rejected in consolidation loop",
                        cost={"token_count": 0, "latency_ms": 0.0},
                    )
                    # Validation Rejection: Invalidate via DAO soft-invalidation (sets invalid_at)
                    record_id = record.get("cmb_id", record.get("id", ""))
                    agent_id = record.get("agent_id", self._agent_id)
                    try:
                        if not record.get("candidate_id"):
                            await self.dao.invalidate_node(agent_id, node_id=record_id)
                    except sqlite3.OperationalError as db_exc:
                        if "database is locked" in str(db_exc):
                            # Infrastructure error (SQLite WAL lock) -> Do NOT mark as invalid, skip so it retries
                            logger.error(
                                "Database locked during invalidate_node for %s",
                                record_id,
                            )
                        else:
                            logger.error(
                                "INVALIDATE_FAILED | id=%s error=%s", record_id, db_exc
                            )
                    except Exception as inv_exc:
                        logger.error(
                            "INVALIDATE_FAILED | id=%s error=%s",
                            record_id,
                            inv_exc,
                        )
                    outcome["rejected"].append(str(record_id))
            else:
                ready_batch.append(record)
                outcome["accepted"].append(
                    str(record.get("cmb_id", record.get("id", "")))
                )

        batch = ready_batch
        if not batch:
            return outcome

        canonical_batch = [record for record in batch if record.get("mutation_id")]
        return await self._run_canonical_v4_batch(canonical_batch, outcome)

    @staticmethod
    def _to_dict(triplet: ExtractedTriplet | None) -> dict:
        """Convert an ExtractedTriplet to a plain dict, or empty sentinel."""
        if triplet:
            return triplet.model_dump(
                exclude={"record_index", "additional_triplets"},
                exclude_none=True,
                exclude_defaults=True,
            )
        return {"head": "", "relation": "", "tail": ""}

    @staticmethod
    def _canonical_triplets(facts: list[FactCandidate]) -> list[dict[str, Any]]:
        """Map facts to the existing V4 assertion representation losslessly."""
        return [
            {
                "head": fact.subject,
                "relation": fact.predicate,
                "tail": fact.object,
                "confidence": fact.confidence,
                "fact_text": fact.fact_text,
                "valid_from": fact.valid_from,
                "valid_to": fact.valid_to,
                "source_span": fact.source_span,
                "supersedes": fact.supersedes,
                "metadata": fact.metadata,
                "object_type": fact.object_type,
            }
            for fact in facts
        ]

    async def _run_canonical_v4_batch(
        self, batch: list[dict[str, Any]], outcome: dict[str, list[str]]
    ) -> dict[str, list[str]]:
        """Extract and persist V4 facts without legacy extractor dependencies."""
        for record in batch:
            candidate_id = str(record.get("cmb_id", record.get("id", "")))
            try:
                facts = await self.fact_extraction_service.extract_facts_from_record(
                    record
                )
                await self.dao.record_mutation_extraction(
                    str(record["agent_id"]),
                    str(record["mutation_id"]),
                    self._canonical_triplets(facts),
                )
            except FactExtractionError as exc:
                logger.error(
                    "CANONICAL_FACT_EXTRACTION_FAILED | id=%s error=%s",
                    candidate_id,
                    exc,
                )
                await self.dead_letter_queue.aappend(
                    {
                        "cmb_id": candidate_id,
                        "agent_id": record.get("agent_id", self._agent_id),
                        "error": str(exc),
                    }
                )
                if candidate_id in outcome["accepted"]:
                    outcome["accepted"].remove(candidate_id)
                outcome["deferred"].append(candidate_id)
        return outcome

    # -------------------------------------------------------------------
    # Async Dual-LLM validation with timeout protection
    # -------------------------------------------------------------------

    @retry(
        wait=wait_exponential(
            multiplier=1, min=config.retry_min_wait_sec, max=config.retry_max_wait_sec
        ),
        stop=stop_after_attempt(config.retry_max_attempts),
    )
    async def _validate_with_timeout(
        self,
        record: dict,
        timeout_seconds: float = 30.0,
    ) -> Any:
        """Run validation with a hard timeout and retry logic obeying ValidationPolicy.

        Wraps the validator call with ``asyncio.wait_for`` and limits
        concurrent execution via semaphore. Automatically retries
        on transient faults (API limits, network jitter).
        """
        record_mode = record.get("validation_mode")
        policy = self._policy_for_record_mode(record_mode)
        # Mode 0 short-circuit: zero LLM calls, no semaphore, no timeout
        if policy.mode == 0:
            return await DeterministicOnlyValidationPolicy().validate_with_audit(record)

        if llm_circuit_breaker.is_open:
            raise Exception("Circuit breaker is OPEN. Failing fast.")
        async with self._llm_semaphore:
            try:
                res: Any
                if policy is not self.validation_policy:
                    res = await asyncio.wait_for(
                        policy.validate_with_audit(record), timeout=timeout_seconds
                    )
                else:
                    res = await asyncio.wait_for(
                        self.router.validate(record),
                        timeout=timeout_seconds,
                    )
                llm_circuit_breaker.record_success()
                return res
            except Exception:
                llm_circuit_breaker.record_failure()
                raise

    def _policy_for_record_mode(self, record_mode: Any) -> ValidationPolicy:
        """Resolve durable policy snapshots without weakening their assurance."""
        if record_mode is None or record_mode == self.validation_policy.mode:
            return self.validation_policy
        if not isinstance(record_mode, int) or record_mode not in (0, 1, 2):
            raise Tier3ValidationError("persisted validation mode is invalid")
        return compose_validation_policy(record_mode)


def _requires_provenance_dual_review(record: dict[str, Any]) -> bool:
    """Reserve dual consensus for high-value, source-backed technical memory.

    Provenance is a relevance signal, not a trust bypass: both validators still
    receive the untrusted-content instruction and must make the final decision.
    """
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return False
    if metadata.get("memory_type") not in {"architecture", "constraint", "decision"}:
        return False
    importance = metadata.get("importance")
    if isinstance(importance, bool) or not isinstance(importance, (int, float)):
        return False
    source_ref = record.get("source_ref")
    evidence_span = record.get("evidence_span")
    return (
        importance >= 0.7
        and isinstance(source_ref, str)
        and bool(source_ref.strip())
        and not source_ref.startswith(("raw-log:", "mcp_tool"))
        and isinstance(evidence_span, str)
        and bool(evidence_span.strip())
    )


async def start_tier3_deferred_worker(
    dao: MemoryDAO,
    consolidation_loop: ConsolidationLoop,
    agent_id: str = "mesa_consolidation_system",
    sleep_interval: int = 5,
    batch_size: int = 10,
):
    """
    Background worker that continuously consumes and processes records marked
    with the compatibility field ``tier3_deferred=True``. The composed policy
    determines whether one or two validation LLMs participate.

    Reads exclusively from ``MemoryDAO`` — the single source of truth.
    """
    logger.info("Starting Tier-3 Deferred background worker...")
    while True:
        try:
            # Read unconsolidated records from the DAO hot-path
            records = await dao.get_memories(
                agent_id,
                include_consolidated=False,
                limit=100,
            )
            deferred_records = [r for r in records if r.get("tier3_deferred")]

            if deferred_records:
                batch = deferred_records[:batch_size]
                logger.debug(
                    f"Worker fetched {len(deferred_records)} deferred records."
                )
                logger.info(f"Worker processing {len(batch)} deferred records.")
                await consolidation_loop.run_batch(batch)

                # Mark processed records as consolidated to prevent
                # re-processing in subsequent polls
                for record in batch:
                    record_id = record.get("cmb_id", record.get("id", ""))
                    record_agent = record.get("agent_id", agent_id)
                    try:
                        await dao.mark_consolidated(record_agent, node_id=record_id)
                    except Exception as mark_exc:
                        logger.error(
                            "Failed to mark deferred record %s: %s",
                            record_id,
                            mark_exc,
                        )
            else:
                await asyncio.sleep(sleep_interval)

        except asyncio.CancelledError:
            logger.info("Tier-3 Deferred worker cancelled, shutting down.")
            break
        except Exception as e:
            logger.error(
                "Error in Tier-3 Deferred worker | exception_type=%s",
                type(e).__name__,
                exc_info=True,
            )
            await asyncio.sleep(sleep_interval)


async def start_dlq_worker(
    dao: MemoryDAO,
    consolidation_loop: ConsolidationLoop,
    agent_id: str = "mesa_consolidation_system",
    sleep_interval: int = 60,
    batch_size: int = 10,
):
    """Replay only leased DLQ records and retain unverified outcomes."""
    worker_id = f"dlq-worker:{agent_id}"
    logger.info("Starting DLQ re-processing background worker...")
    while True:
        try:
            claims = await consolidation_loop.dead_letter_queue.aclaim(
                worker_id=worker_id, limit=batch_size
            )
            if not claims:
                await asyncio.sleep(sleep_interval)
                continue
            batch_records: list[dict] = []
            replayable_claims: list[dict] = []
            for claim in claims:
                if await consolidation_loop.dead_letter_queue.areconcile_receipted_claim(
                    claim, worker_id=worker_id
                ):
                    continue
                record_agent_id = claim.get("agent_id")
                cmb_id = claim.get("cmb_id")
                if not record_agent_id or not cmb_id:
                    await consolidation_loop.dead_letter_queue.anack(
                        [claim], worker_id=worker_id, error_type="InvalidDLQMetadata"
                    )
                    continue
                record = await dao.get_memory_by_id(record_agent_id, cmb_id)
                if record is None:
                    await consolidation_loop.dead_letter_queue.anack(
                        [claim], worker_id=worker_id, error_type="RecordNotFound"
                    )
                    continue
                batch_records.append(record)
                replayable_claims.append(claim)
            for record, claim in zip(batch_records, replayable_claims):
                try:
                    await consolidation_loop.run_batch([record])
                    verified = await dao.get_memory_by_id(
                        claim["agent_id"], claim["cmb_id"]
                    )
                    side_effect_verified = bool(
                        verified and verified.get("is_consolidated")
                    )
                    if not await consolidation_loop.dead_letter_queue.acomplete(
                        claim,
                        worker_id=worker_id,
                        outcome="SUCCEEDED",
                        side_effect_verified=side_effect_verified,
                    ):
                        await consolidation_loop.dead_letter_queue.anack(
                            [claim],
                            worker_id=worker_id,
                            error_type="UnverifiedRecordOutcome",
                        )
                except Exception as exc:
                    await consolidation_loop.dead_letter_queue.anack(
                        [claim], worker_id=worker_id, error_type=type(exc).__name__
                    )
            await asyncio.sleep(sleep_interval)
        except asyncio.CancelledError:
            logger.info("DLQ worker cancelled, shutting down.")
            break
        except Exception as exc:
            logger.error(
                "DLQ worker failure type=%s", type(exc).__name__, exc_info=True
            )
            await asyncio.sleep(sleep_interval)
