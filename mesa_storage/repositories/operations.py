"""Durable, fenced system-operation state machine owned by SQLite."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from enum import Enum
from typing import Any, Protocol

import aiosqlite

from mesa_storage.sqlite_engine import AsyncEngine

_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ERROR_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_ACTIVE_EXECUTION_STATES = frozenset(
    {"CLAIMED", "RUNNING", "VERIFYING", "READY_TO_CUTOVER"}
)
_ALLOWED_TRANSITIONS = {
    "CLAIMED": frozenset({"RUNNING", "RETRYABLE_FAILED", "FINAL_FAILED"}),
    "RUNNING": frozenset({"RUNNING", "VERIFYING", "RETRYABLE_FAILED", "FINAL_FAILED"}),
    "VERIFYING": frozenset(
        {"VERIFYING", "READY_TO_CUTOVER", "RETRYABLE_FAILED", "FINAL_FAILED"}
    ),
    "READY_TO_CUTOVER": frozenset(
        {"READY_TO_CUTOVER", "COMPLETED", "RETRYABLE_FAILED", "FINAL_FAILED"}
    ),
}
_FINAL_STATES = frozenset(
    {"COMPLETED", "RETRYABLE_FAILED", "FINAL_FAILED", "CANCELLED"}
)


class OperationState(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    READY_TO_CUTOVER = "READY_TO_CUTOVER"
    COMPLETED = "COMPLETED"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    FINAL_FAILED = "FINAL_FAILED"
    CANCELLED = "CANCELLED"


class OperationRepositoryError(RuntimeError):
    """Base class for stable, content-free operation outcomes."""


class OperationNotFoundError(OperationRepositoryError):
    """The requested operation does not exist."""


class OperationConflictError(OperationRepositoryError):
    """A durable operation conflicts with an existing request or owner."""


class OperationIdempotencyConflictError(OperationConflictError):
    """An idempotency key was reused with a different payload hash."""


class OperationActiveConflictError(OperationConflictError):
    """The storage root already has an active projection rebuild."""


class OperationStateError(OperationRepositoryError):
    """The requested state transition is not valid."""


class OperationFencedError(OperationRepositoryError):
    """The caller no longer owns the operation lease and fence."""


class OperationRepositoryPort(Protocol):
    """The complete application-facing system-operation persistence surface."""

    async def submit(
        self,
        *,
        requested_by_principal_id: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> dict[str, Any]: ...

    async def get(self, operation_id: str) -> dict[str, Any] | None: ...

    async def claim(
        self, operation_id: str, *, runner_id: str, lease_seconds: int = 60
    ) -> dict[str, Any]: ...

    async def renew(
        self,
        operation_id: str,
        *,
        runner_id: str,
        claim_token: str,
        fencing_token: int,
        lease_seconds: int = 60,
    ) -> dict[str, Any]: ...

    async def transition(
        self,
        operation_id: str,
        *,
        to_state: OperationState | str,
        runner_id: str,
        claim_token: str,
        fencing_token: int,
        progress_completed: int | None = None,
        progress_total: int | None = None,
        checkpoint: dict[str, Any] | None = None,
        source_manifest_hash: str | None = None,
        source_manifest: dict[str, Any] | None = None,
        source_generation_id: str | None = None,
        target_generation_id: str | None = None,
        error_class: str | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any]: ...

    async def cancel(self, operation_id: str) -> dict[str, Any]: ...

    async def retry(self, operation_id: str) -> dict[str, Any]: ...


def _bounded(value: str, *, label: str, limit: int = 128) -> str:
    if not value or len(value) > limit or any(ord(char) < 32 for char in value):
        raise ValueError(f"{label} is invalid")
    return value


def _hash(value: str, *, label: str) -> str:
    normalized = value.lower()
    if not _HASH_PATTERN.fullmatch(normalized):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return normalized


def _error_value(value: str | None, *, label: str) -> str | None:
    if value is None:
        return None
    if not _ERROR_PATTERN.fullmatch(value):
        raise ValueError(f"{label} is invalid")
    return value


def _canonical_json(value: dict[str, Any], *, label: str) -> tuple[str, str]:
    try:
        serialized = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be JSON serializable") from exc
    if len(serialized.encode("utf-8")) > 65_536:
        raise ValueError(f"{label} exceeds the durable size limit")
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return serialized, digest


def _decode(row: aiosqlite.Row) -> dict[str, Any]:
    result = dict(row)
    result.pop("lease_valid", None)
    result["checkpoint"] = json.loads(result.pop("checkpoint_json"))
    result["source_manifest"] = json.loads(result.pop("source_manifest_json"))
    return result


class OperationRepository:
    """Own all operation transactions, leases, fences and audit events."""

    __slots__ = ("_sql",)

    def __init__(self, sqlite_engine: AsyncEngine) -> None:
        self._sql = sqlite_engine

    async def _row(
        self, db: aiosqlite.Connection, operation_id: str
    ) -> aiosqlite.Row | None:
        cursor = await db.execute(
            "SELECT *, CASE WHEN lease_expires_at > CURRENT_TIMESTAMP "
            "THEN 1 ELSE 0 END AS lease_valid "
            "FROM system_operations WHERE operation_id = ?",
            (operation_id,),
        )
        return await cursor.fetchone()

    async def _event(
        self,
        db: aiosqlite.Connection,
        *,
        operation_id: str,
        event_type: str,
        from_state: str | None,
        to_state: str,
        fencing_token: int,
        attempt_count: int,
        progress_completed: int,
        progress_total: int,
        checkpoint_hash: str | None = None,
        error_class: str | None = None,
    ) -> None:
        cursor = await db.execute(
            "SELECT COALESCE(MAX(sequence_number), 0) + 1 "
            "FROM system_operation_events WHERE operation_id = ?",
            (operation_id,),
        )
        sequence_row = await cursor.fetchone()
        assert sequence_row is not None
        sequence = int(sequence_row[0])
        await db.execute(
            "INSERT INTO system_operation_events ("
            "event_id, operation_id, sequence_number, event_type, from_state, "
            "to_state, fencing_token, attempt_count, progress_completed, "
            "progress_total, checkpoint_hash, error_class"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                operation_id,
                sequence,
                event_type,
                from_state,
                to_state,
                fencing_token,
                attempt_count,
                progress_completed,
                progress_total,
                checkpoint_hash,
                error_class,
            ),
        )

    async def submit(
        self,
        *,
        requested_by_principal_id: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> dict[str, Any]:
        principal = _bounded(requested_by_principal_id, label="requested principal")
        key = _bounded(idempotency_key, label="idempotency key")
        digest = _hash(payload_hash, label="payload hash")
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                "SELECT * FROM system_operations WHERE operation_kind = "
                "'PROJECTION_REBUILD' AND scope_kind = 'STORAGE_ROOT' "
                "AND scope_key = 'default' AND idempotency_key = ?",
                (key,),
            )
            existing = await cursor.fetchone()
            if existing is not None:
                if existing["payload_hash"] != digest:
                    raise OperationIdempotencyConflictError(
                        "idempotency key conflicts with another rebuild payload"
                    )
                await db.commit()
                return _decode(existing)

            operation_id = str(uuid.uuid4())
            try:
                await db.execute(
                    "INSERT INTO system_operations ("
                    "operation_id, operation_kind, scope_kind, scope_key, "
                    "requested_by_principal_id, idempotency_key, payload_hash, state"
                    ") VALUES (?, 'PROJECTION_REBUILD', 'STORAGE_ROOT', "
                    "'default', ?, ?, ?, 'PENDING')",
                    (operation_id, principal, key, digest),
                )
            except sqlite3.IntegrityError as exc:
                raise OperationActiveConflictError(
                    "storage root already has an active projection rebuild"
                ) from exc
            await self._event(
                db,
                operation_id=operation_id,
                event_type="SUBMITTED",
                from_state=None,
                to_state="PENDING",
                fencing_token=0,
                attempt_count=0,
                progress_completed=0,
                progress_total=0,
            )
            await db.commit()
            row = await self._row(db, operation_id)
            assert row is not None
            return _decode(row)

    async def get(self, operation_id: str) -> dict[str, Any] | None:
        identifier = _bounded(operation_id, label="operation id")
        async with self._sql.connection() as db:
            row = await self._row(db, identifier)
        return _decode(row) if row is not None else None

    async def claim(
        self, operation_id: str, *, runner_id: str, lease_seconds: int = 60
    ) -> dict[str, Any]:
        identifier = _bounded(operation_id, label="operation id")
        runner = _bounded(runner_id, label="runner id")
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease seconds must be between 1 and 3600")
        async with self._sql.transaction() as db:
            row = await self._row(db, identifier)
            if row is None:
                raise OperationNotFoundError("operation does not exist")
            state = str(row["state"])
            if state == "PENDING":
                next_state = "CLAIMED"
                event_type = "CLAIMED"
            elif state in _ACTIVE_EXECUTION_STATES and not bool(row["lease_valid"]):
                next_state = state
                event_type = "RECLAIMED"
            else:
                raise OperationStateError("operation is not claimable")
            claim_token = str(uuid.uuid4())
            fencing_token = int(row["fencing_token"]) + 1
            attempt_count = int(row["attempt_count"]) + 1
            cursor = await db.execute(
                "UPDATE system_operations SET state = ?, claimed_by = ?, "
                "claim_token = ?, fencing_token = ?, attempt_count = ?, "
                "lease_expires_at = datetime('now', ?), "
                "claimed_at = COALESCE(claimed_at, CURRENT_TIMESTAMP), "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE operation_id = ? AND fencing_token = ?",
                (
                    next_state,
                    runner,
                    claim_token,
                    fencing_token,
                    attempt_count,
                    f"+{lease_seconds} seconds",
                    identifier,
                    row["fencing_token"],
                ),
            )
            if cursor.rowcount != 1:
                raise OperationFencedError("operation claim was fenced out")
            await self._event(
                db,
                operation_id=identifier,
                event_type=event_type,
                from_state=state,
                to_state=next_state,
                fencing_token=fencing_token,
                attempt_count=attempt_count,
                progress_completed=int(row["progress_completed"]),
                progress_total=int(row["progress_total"]),
            )
            await db.commit()
            claimed = await self._row(db, identifier)
            assert claimed is not None
            return _decode(claimed)

    @staticmethod
    def _assert_fence(
        row: aiosqlite.Row,
        *,
        runner_id: str,
        claim_token: str,
        fencing_token: int,
    ) -> None:
        if (
            row["claimed_by"] != runner_id
            or row["claim_token"] != claim_token
            or int(row["fencing_token"]) != fencing_token
            or not bool(row["lease_valid"])
        ):
            raise OperationFencedError("operation lease or fence is stale")

    async def renew(
        self,
        operation_id: str,
        *,
        runner_id: str,
        claim_token: str,
        fencing_token: int,
        lease_seconds: int = 60,
    ) -> dict[str, Any]:
        identifier = _bounded(operation_id, label="operation id")
        runner = _bounded(runner_id, label="runner id")
        token = _bounded(claim_token, label="claim token")
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease seconds must be between 1 and 3600")
        async with self._sql.transaction() as db:
            row = await self._row(db, identifier)
            if row is None:
                raise OperationNotFoundError("operation does not exist")
            if str(row["state"]) not in _ACTIVE_EXECUTION_STATES:
                raise OperationStateError("operation lease cannot be renewed")
            self._assert_fence(
                row,
                runner_id=runner,
                claim_token=token,
                fencing_token=fencing_token,
            )
            cursor = await db.execute(
                "UPDATE system_operations SET lease_expires_at = datetime('now', ?), "
                "updated_at = CURRENT_TIMESTAMP WHERE operation_id = ? "
                "AND claimed_by = ? AND claim_token = ? AND fencing_token = ? "
                "AND lease_expires_at > CURRENT_TIMESTAMP",
                (
                    f"+{lease_seconds} seconds",
                    identifier,
                    runner,
                    token,
                    fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise OperationFencedError("operation renewal was fenced out")
            await db.commit()
            renewed = await self._row(db, identifier)
            assert renewed is not None
            return _decode(renewed)

    async def transition(
        self,
        operation_id: str,
        *,
        to_state: OperationState | str,
        runner_id: str,
        claim_token: str,
        fencing_token: int,
        progress_completed: int | None = None,
        progress_total: int | None = None,
        checkpoint: dict[str, Any] | None = None,
        source_manifest_hash: str | None = None,
        source_manifest: dict[str, Any] | None = None,
        source_generation_id: str | None = None,
        target_generation_id: str | None = None,
        error_class: str | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        identifier = _bounded(operation_id, label="operation id")
        runner = _bounded(runner_id, label="runner id")
        token = _bounded(claim_token, label="claim token")
        try:
            target_state = OperationState(to_state).value
        except ValueError as exc:
            raise OperationStateError("unknown operation state") from exc
        normalized_error_class = _error_value(error_class, label="error class")
        normalized_error_code = _error_value(error_code, label="error code")
        manifest_hash = (
            _hash(source_manifest_hash, label="source manifest hash")
            if source_manifest_hash is not None
            else None
        )
        source_generation = (
            _bounded(source_generation_id, label="source generation id")
            if source_generation_id is not None
            else None
        )
        target_generation = (
            _bounded(target_generation_id, label="target generation id")
            if target_generation_id is not None
            else None
        )
        checkpoint_json: str | None = None
        checkpoint_hash: str | None = None
        if checkpoint is not None:
            checkpoint_json, checkpoint_hash = _canonical_json(
                checkpoint, label="checkpoint"
            )
        manifest_json: str | None = None
        if source_manifest is not None:
            manifest_json, _ = _canonical_json(source_manifest, label="source manifest")

        async with self._sql.transaction() as db:
            row = await self._row(db, identifier)
            if row is None:
                raise OperationNotFoundError("operation does not exist")
            current_state = str(row["state"])
            self._assert_fence(
                row,
                runner_id=runner,
                claim_token=token,
                fencing_token=fencing_token,
            )
            if target_state not in _ALLOWED_TRANSITIONS.get(current_state, frozenset()):
                raise OperationStateError("operation transition is not allowed")
            if target_state.endswith("FAILED") and normalized_error_class is None:
                raise OperationStateError("failed operation requires an error class")
            if not target_state.endswith("FAILED") and (
                normalized_error_class is not None or normalized_error_code is not None
            ):
                raise OperationStateError(
                    "non-failure transition cannot record an error"
                )
            if target_state == "RETRYABLE_FAILED" and int(row["attempt_count"]) >= int(
                row["retry_limit"]
            ):
                raise OperationStateError("operation retry budget is exhausted")

            completed = (
                int(row["progress_completed"])
                if progress_completed is None
                else progress_completed
            )
            total = (
                int(row["progress_total"]) if progress_total is None else progress_total
            )
            if completed < int(row["progress_completed"]):
                raise OperationStateError("operation progress cannot move backwards")
            if int(row["progress_total"]) and total != int(row["progress_total"]):
                raise OperationStateError("operation progress total cannot change")
            if completed < 0 or total < completed:
                raise OperationStateError("operation progress is invalid")
            if target_state in {"VERIFYING", "READY_TO_CUTOVER", "COMPLETED"} and (
                completed != total
            ):
                raise OperationStateError(
                    "operation must finish replay before verification or cutover"
                )
            if (
                target_state == current_state
                and checkpoint_json is None
                and completed == int(row["progress_completed"])
                and total == int(row["progress_total"])
            ):
                raise OperationStateError("same-state transition requires a checkpoint")

            assignments = [
                "state = ?",
                "progress_completed = ?",
                "progress_total = ?",
                "updated_at = CURRENT_TIMESTAMP",
            ]
            values: list[Any] = [target_state, completed, total]
            optional = (
                ("checkpoint_json", checkpoint_json),
                ("source_manifest_hash", manifest_hash),
                ("source_manifest_json", manifest_json),
                ("source_generation_id", source_generation),
                ("target_generation_id", target_generation),
                ("last_error_class", normalized_error_class),
                ("last_error_code", normalized_error_code),
            )
            for column, value in optional:
                if value is not None:
                    assignments.append(f"{column} = ?")
                    values.append(value)
            timestamp_column = {
                "RUNNING": "started_at",
                "VERIFYING": "verifying_at",
                "READY_TO_CUTOVER": "ready_at",
                "COMPLETED": "completed_at",
            }.get(target_state)
            if timestamp_column is not None:
                assignments.append(
                    f"{timestamp_column} = COALESCE({timestamp_column}, CURRENT_TIMESTAMP)"
                )
            if target_state in _FINAL_STATES:
                assignments.extend(
                    [
                        "claimed_by = NULL",
                        "claim_token = NULL",
                        "lease_expires_at = NULL",
                    ]
                )
            values.extend((identifier, runner, token, fencing_token))
            cursor = await db.execute(
                f"UPDATE system_operations SET {', '.join(assignments)} "
                "WHERE operation_id = ? AND claimed_by = ? AND claim_token = ? "
                "AND fencing_token = ? AND lease_expires_at > CURRENT_TIMESTAMP",
                values,
            )
            if cursor.rowcount != 1:
                raise OperationFencedError("operation transition was fenced out")
            await self._event(
                db,
                operation_id=identifier,
                event_type=(
                    "CHECKPOINTED" if target_state == current_state else "TRANSITIONED"
                ),
                from_state=current_state,
                to_state=target_state,
                fencing_token=fencing_token,
                attempt_count=int(row["attempt_count"]),
                progress_completed=completed,
                progress_total=total,
                checkpoint_hash=checkpoint_hash,
                error_class=normalized_error_class,
            )
            await db.commit()
            transitioned = await self._row(db, identifier)
            assert transitioned is not None
            return _decode(transitioned)

    async def cancel(self, operation_id: str) -> dict[str, Any]:
        identifier = _bounded(operation_id, label="operation id")
        async with self._sql.transaction() as db:
            row = await self._row(db, identifier)
            if row is None:
                raise OperationNotFoundError("operation does not exist")
            state = str(row["state"])
            if state not in {"PENDING", "RETRYABLE_FAILED"}:
                raise OperationStateError("operation cannot be cancelled")
            await db.execute(
                "UPDATE system_operations SET state = 'CANCELLED', "
                "claimed_by = NULL, claim_token = NULL, lease_expires_at = NULL, "
                "cancelled_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
                "WHERE operation_id = ? AND state = ?",
                (identifier, state),
            )
            await self._event(
                db,
                operation_id=identifier,
                event_type="CANCELLED",
                from_state=state,
                to_state="CANCELLED",
                fencing_token=int(row["fencing_token"]),
                attempt_count=int(row["attempt_count"]),
                progress_completed=int(row["progress_completed"]),
                progress_total=int(row["progress_total"]),
            )
            await db.commit()
            cancelled = await self._row(db, identifier)
            assert cancelled is not None
            return _decode(cancelled)

    async def retry(self, operation_id: str) -> dict[str, Any]:
        identifier = _bounded(operation_id, label="operation id")
        async with self._sql.transaction() as db:
            row = await self._row(db, identifier)
            if row is None:
                raise OperationNotFoundError("operation does not exist")
            if str(row["state"]) != "RETRYABLE_FAILED":
                raise OperationStateError("operation is not retryable")
            if int(row["attempt_count"]) >= int(row["retry_limit"]):
                raise OperationStateError("operation retry budget is exhausted")
            await db.execute(
                "UPDATE system_operations SET state = 'PENDING', claimed_by = NULL, "
                "claim_token = NULL, lease_expires_at = NULL, "
                "last_error_class = NULL, last_error_code = NULL, "
                "updated_at = CURRENT_TIMESTAMP WHERE operation_id = ? "
                "AND state = 'RETRYABLE_FAILED'",
                (identifier,),
            )
            await self._event(
                db,
                operation_id=identifier,
                event_type="RETRIED",
                from_state="RETRYABLE_FAILED",
                to_state="PENDING",
                fencing_token=int(row["fencing_token"]),
                attempt_count=int(row["attempt_count"]),
                progress_completed=int(row["progress_completed"]),
                progress_total=int(row["progress_total"]),
            )
            await db.commit()
            retried = await self._row(db, identifier)
            assert retried is not None
            return _decode(retried)
