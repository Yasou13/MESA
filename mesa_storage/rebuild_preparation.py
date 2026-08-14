"""Offline projection-rebuild preflight, backup and durable preparation."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from mesa_storage.projection_generations import (
    ProjectionGenerationRepositoryPort,
)
from mesa_storage.recovery import (
    MANIFEST_NAME,
    create_backup,
    validate_snapshot,
)
from mesa_storage.repositories.operations import OperationRepositoryPort
from mesa_storage.writer_lock import StorageWriterLock

REBUILD_ALEMBIC_HEAD = "fe5f6a7b8c9d"
_ADMIN_TABLES = frozenset(
    {
        "system_operations",
        "system_operation_events",
        "projection_generations",
        "projection_runtime",
    }
)
_MIN_FREE_RESERVE_BYTES = 16 * 1024 * 1024
_OPERATION_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class RebuildPreparationError(RuntimeError):
    """A content-free, operator-correctable rebuild preparation failure."""


class RebuildPathError(RebuildPreparationError):
    """A rebuild path cannot be proven to remain inside its trust boundary."""


class RebuildBacklogError(RebuildPreparationError):
    """Durable worker or cleanup work has not drained."""


class RebuildDiskCapacityError(RebuildPreparationError):
    """The work filesystem cannot safely hold backup and staging stores."""


class RebuildSourceChangedError(RebuildPreparationError):
    """The canonical source changed after the durable checkpoint was written."""


@dataclass(frozen=True)
class RebuildPreflight:
    source_manifest: dict[str, Any]
    source_manifest_hash: str
    source_generation_id: str
    runtime_fencing_token: int
    storage_bytes: int
    required_free_bytes: int


@dataclass(frozen=True)
class RebuildPreparation:
    operation: dict[str, Any]
    generation: dict[str, Any]
    backup_root: Path
    backup_manifest_hash: str
    source_manifest: dict[str, Any]
    source_manifest_hash: str
    source_generation_id: str
    target_generation_id: str
    runtime_fencing_token: int


def _safe_existing_child(path: Path, trusted_root: Path, *, label: str) -> Path:
    try:
        trusted = trusted_root.resolve(strict=True)
        candidate = path.resolve(strict=True)
    except OSError as exc:
        raise RebuildPathError(f"{label} does not exist") from exc
    if not trusted.is_dir() or not candidate.is_dir():
        raise RebuildPathError(f"{label} must be a directory")
    if candidate == trusted or not candidate.is_relative_to(trusted):
        raise RebuildPathError(f"{label} escapes the trusted root")
    current = path.absolute()
    while current != trusted:
        if current.is_symlink():
            raise RebuildPathError(f"{label} contains a symlink")
        if current.parent == current:
            raise RebuildPathError(f"{label} escapes the trusted root")
        current = current.parent
    return candidate


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _logical_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"blob_sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
    if value is None or isinstance(value, (str, int, float)):
        return value
    return str(value)


def canonical_sqlite_manifest(database: Path) -> tuple[dict[str, Any], str]:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "INSERT INTO v4_entities_fts(v4_entities_fts, rank) "
                "VALUES ('integrity-check', 1)"
            )
            connection.rollback()
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RebuildPreparationError("canonical FTS integrity failed") from exc
        connection.execute("PRAGMA query_only=ON")
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        missing_fts = int(
            connection.execute(
                "SELECT COUNT(*) FROM v4_entities e "
                "LEFT JOIN v4_entities_fts f ON f.rowid = e.rowid "
                "WHERE f.rowid IS NULL"
            ).fetchone()[0]
        )
        extra_fts = int(
            connection.execute(
                "SELECT COUNT(*) FROM v4_entities_fts f "
                "LEFT JOIN v4_entities e ON e.rowid = f.rowid "
                "WHERE e.rowid IS NULL"
            ).fetchone()[0]
        )
        fts_integrity = "ok" if missing_fts == 0 and extra_fts == 0 else "mismatch"
        head_row = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        head = str(head_row[0]) if head_row else None
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            if str(row[0]) not in _ADMIN_TABLES
        ]
        digest = hashlib.sha256()
        counts: dict[str, int] = {}
        for table in tables:
            quoted = _quote_identifier(table)
            columns = [
                (str(row[1]), int(row[5]))
                for row in connection.execute(f"PRAGMA table_info({quoted})").fetchall()
            ]
            if not columns:
                continue
            primary_key = [
                name
                for name, position in sorted(columns, key=lambda item: item[1])
                if position
            ]
            ordering = (
                ", ".join(_quote_identifier(item) for item in primary_key)
                if primary_key
                else "rowid"
            )
            rows = connection.execute(f"SELECT * FROM {quoted} ORDER BY {ordering}")
            count = 0
            digest.update(f"table:{table}\n".encode())
            for row in rows:
                encoded = json.dumps(
                    [_logical_value(value) for value in row],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
                count += 1
            counts[table] = count
    except sqlite3.DatabaseError as exc:
        raise RebuildPreparationError("canonical SQLite validation failed") from exc
    finally:
        if connection is not None:
            connection.close()
    if integrity != "ok" or fts_integrity != "ok":
        raise RebuildPreparationError("canonical SQLite integrity failed")
    manifest = {
        "format": "mesa-rebuild-source",
        "version": 1,
        "alembic_head": head,
        "canonical_sha256": digest.hexdigest(),
        "table_counts": counts,
        "sqlite_integrity": integrity,
        "fts_integrity": fts_integrity,
    }
    encoded_manifest = json.dumps(
        manifest, sort_keys=True, separators=(",", ":")
    ).encode()
    return manifest, hashlib.sha256(encoded_manifest).hexdigest()


def _backlog_counts(connection: sqlite3.Connection) -> dict[str, int]:
    queries = {
        "projection": (
            "SELECT COUNT(*) FROM projection_outbox WHERE state != 'COMPLETED'"
        ),
        "cleanup": (
            "SELECT COUNT(*) FROM artifact_cleanup_outbox WHERE state != 'COMPLETED'"
        ),
        "dispatch": ("SELECT COUNT(*) FROM dispatch_queue WHERE state != 'FINALIZED'"),
        "vector_wal": "SELECT COUNT(*) FROM lancedb_wal WHERE state != 'ACKED'",
        "session_finalization": (
            "SELECT COUNT(*) FROM session_finalization_journal "
            "WHERE state != 'COMPLETED'"
        ),
        "raw_log": (
            "SELECT COUNT(*) FROM raw_logs WHERE upper(status) IN "
            "('DEFERRED', 'PROCESSING', 'PENDING', 'RETRY_PENDING', 'IN_FLIGHT')"
        ),
    }
    return {
        name: int(connection.execute(statement).fetchone()[0])
        for name, statement in queries.items()
    }


def _storage_size(storage_root: Path) -> int:
    total = 0
    for item in sorted(storage_root.rglob("*")):
        if item.is_symlink():
            raise RebuildPathError("storage root contains a symlink")
        if item.is_file():
            total += item.stat().st_size
    return total


def _dead_letter_count(storage_root: Path) -> int:
    path = storage_root / "dead_letter_queue.jsonl"
    if not path.exists():
        return 0
    if path.is_symlink() or not path.is_file():
        raise RebuildPathError("dead-letter queue path is unsafe")
    with path.open("r", encoding="utf-8") as stream:
        return sum(1 for line in stream if line.strip())


def inspect_rebuild_preflight(
    *,
    trusted_root: Path,
    storage_root: Path,
    work_root: Path,
    operation_id: str,
    runner_id: str,
    claim_token: str,
    operation_fencing_token: int,
    writer_lock: StorageWriterLock,
    disk_usage: Callable[[Path], Any] = shutil.disk_usage,
) -> RebuildPreflight:
    trusted = trusted_root.resolve(strict=True)
    storage = _safe_existing_child(storage_root, trusted, label="storage root")
    work = _safe_existing_child(work_root, trusted, label="work root")
    if storage.is_relative_to(work) or work.is_relative_to(storage):
        raise RebuildPathError("storage and work roots must not overlap")
    if writer_lock.released or writer_lock.storage_root != storage:
        raise RebuildPreparationError("rebuild does not own the storage writer lock")
    database = storage / "mesa.db"
    if not database.is_file() or database.is_symlink():
        raise RebuildPathError("canonical SQLite database is unavailable")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        operation = connection.execute(
            "SELECT *, lease_expires_at > CURRENT_TIMESTAMP AS lease_valid "
            "FROM system_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if (
            operation is None
            or operation["operation_kind"] != "PROJECTION_REBUILD"
            or operation["scope_kind"] != "STORAGE_ROOT"
            or operation["state"] not in {"CLAIMED", "RUNNING"}
            or operation["claimed_by"] != runner_id
            or operation["claim_token"] != claim_token
            or int(operation["fencing_token"]) != operation_fencing_token
            or not bool(operation["lease_valid"])
        ):
            raise RebuildPreparationError("rebuild operation lease or fence is stale")
        head_row = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        if head_row is None or str(head_row[0]) != REBUILD_ALEMBIC_HEAD:
            raise RebuildPreparationError("canonical SQLite is not at rebuild head")
        backlog = _backlog_counts(connection)
        backlog["dead_letter_file"] = _dead_letter_count(storage)
        active = connection.execute(
            "SELECT active_generation_id, fencing_token FROM projection_runtime "
            "WHERE runtime_id = 1"
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        raise RebuildPreparationError("rebuild preflight query failed") from exc
    finally:
        if connection is not None:
            connection.close()
    if any(backlog.values()):
        raise RebuildBacklogError("durable worker backlog has not drained")
    if active is None:
        raise RebuildPreparationError("active projection generation is unavailable")
    source_manifest, source_hash = canonical_sqlite_manifest(database)
    storage_bytes = _storage_size(storage)
    required = storage_bytes * 2 + _MIN_FREE_RESERVE_BYTES
    if int(disk_usage(work).free) < required:
        raise RebuildDiskCapacityError("work filesystem has insufficient free space")
    return RebuildPreflight(
        source_manifest=source_manifest,
        source_manifest_hash=source_hash,
        source_generation_id=str(active[0]),
        runtime_fencing_token=int(active[1]),
        storage_bytes=storage_bytes,
        required_free_bytes=required,
    )


class OfflineRebuildPreparer:
    """Create or resume a checksummed backup and fenced preparation checkpoint."""

    def __init__(
        self,
        operations: OperationRepositoryPort,
        generations: ProjectionGenerationRepositoryPort,
    ) -> None:
        self._operations = operations
        self._generations = generations

    async def prepare(
        self,
        *,
        trusted_root: Path,
        storage_root: Path,
        work_root: Path,
        operation: dict[str, Any],
        runner_id: str,
        writer_lock: StorageWriterLock,
        provider_manifest: dict[str, Any],
        disk_usage: Callable[[Path], Any] = shutil.disk_usage,
    ) -> RebuildPreparation:
        operation_id = str(operation["operation_id"])
        if not _OPERATION_ID_PATTERN.fullmatch(operation_id):
            raise RebuildPathError("operation id is not a safe work identifier")
        claim_token = str(operation["claim_token"])
        fencing_token = int(operation["fencing_token"])
        preflight = inspect_rebuild_preflight(
            trusted_root=trusted_root,
            storage_root=storage_root,
            work_root=work_root,
            operation_id=operation_id,
            runner_id=runner_id,
            claim_token=claim_token,
            operation_fencing_token=fencing_token,
            writer_lock=writer_lock,
            disk_usage=disk_usage,
        )
        existing_hash = operation.get("source_manifest_hash")
        if (
            existing_hash is not None
            and existing_hash != preflight.source_manifest_hash
        ):
            raise RebuildSourceChangedError("canonical source manifest changed")

        operation_work_root = work_root.resolve(strict=True) / operation_id
        backup_root = operation_work_root / "backup"
        operation_work_root.mkdir(mode=0o700, exist_ok=True)
        if backup_root.exists():
            validate_snapshot(backup_root)
        else:
            create_backup(
                storage_root,
                backup_root,
                trusted_root,
                stores_stopped=True,
            )
        backup_manifest_hash = hashlib.sha256(
            (backup_root / MANIFEST_NAME).read_bytes()
        ).hexdigest()
        backup_manifest, backup_source_hash = canonical_sqlite_manifest(
            backup_root / "mesa.db"
        )
        if backup_source_hash != preflight.source_manifest_hash:
            raise RebuildSourceChangedError("backup source manifest does not match")

        target_generation_id = f"rebuild-{operation_id}"
        checkpoint = {
            **dict(operation.get("checkpoint") or {}),
            "phase": "BACKUP_VERIFIED",
            "backup_manifest_sha256": backup_manifest_hash,
            "source_manifest_hash": preflight.source_manifest_hash,
            "staging_bytes": 0,
        }
        operation = await self._operations.transition(
            operation_id,
            to_state="RUNNING",
            runner_id=runner_id,
            claim_token=claim_token,
            fencing_token=fencing_token,
            checkpoint=checkpoint,
            source_manifest_hash=preflight.source_manifest_hash,
            source_manifest=preflight.source_manifest,
            source_generation_id=preflight.source_generation_id,
        )
        generation = await self._generations.create_staging(
            operation_id=operation_id,
            generation_id=target_generation_id,
            runner_id=runner_id,
            claim_token=claim_token,
            operation_fencing_token=fencing_token,
            source_manifest_hash=preflight.source_manifest_hash,
            provider_manifest=provider_manifest,
        )
        checkpoint["phase"] = "PREPARED"
        checkpoint["target_generation_id"] = target_generation_id
        operation = await self._operations.transition(
            operation_id,
            to_state="RUNNING",
            runner_id=runner_id,
            claim_token=claim_token,
            fencing_token=fencing_token,
            checkpoint=checkpoint,
            target_generation_id=target_generation_id,
        )
        return RebuildPreparation(
            operation=operation,
            generation=generation,
            backup_root=backup_root,
            backup_manifest_hash=backup_manifest_hash,
            source_manifest=backup_manifest,
            source_manifest_hash=preflight.source_manifest_hash,
            source_generation_id=preflight.source_generation_id,
            target_generation_id=target_generation_id,
            runtime_fencing_token=preflight.runtime_fencing_token,
        )


async def resume_cutover_preparation(
    *,
    trusted_root: Path,
    storage_root: Path,
    work_root: Path,
    operation: dict[str, Any],
    generations: ProjectionGenerationRepositoryPort,
    writer_lock: StorageWriterLock,
) -> RebuildPreparation:
    """Reconstruct only verified, durable inputs after a cutover-stage crash."""
    operation_id = str(operation.get("operation_id") or "")
    if not _OPERATION_ID_PATTERN.fullmatch(operation_id):
        raise RebuildPathError("operation id is not a safe work identifier")
    if operation.get("state") != "READY_TO_CUTOVER":
        raise RebuildPreparationError("operation is not ready for cutover recovery")
    trusted = trusted_root.resolve(strict=True)
    storage = _safe_existing_child(storage_root, trusted, label="storage root")
    work = _safe_existing_child(work_root, trusted, label="work root")
    if storage.is_relative_to(work) or work.is_relative_to(storage):
        raise RebuildPathError("storage and work roots must not overlap")
    if writer_lock.released or writer_lock.storage_root != storage:
        raise RebuildPreparationError("rebuild does not own the storage writer lock")

    source_generation_id = str(operation.get("source_generation_id") or "")
    target_generation_id = str(operation.get("target_generation_id") or "")
    if not source_generation_id or target_generation_id != f"rebuild-{operation_id}":
        raise RebuildPreparationError("cutover generation identity is unavailable")
    active = await generations.resolve_active(
        storage_root=storage, trusted_root=trusted
    )
    if active.generation_id == target_generation_id:
        if active.previous_generation_id != source_generation_id:
            raise RebuildPreparationError("retained generation pointer is unavailable")
    elif active.generation_id != source_generation_id:
        raise RebuildPreparationError("runtime pointer is outside the cutover pair")

    backup_root = _safe_existing_child(
        work / operation_id / "backup", work, label="backup root"
    )
    validate_snapshot(backup_root)
    checkpoint = dict(operation.get("checkpoint") or {})
    backup_manifest_hash = hashlib.sha256(
        (backup_root / MANIFEST_NAME).read_bytes()
    ).hexdigest()
    if checkpoint.get("backup_manifest_sha256") != backup_manifest_hash:
        raise RebuildSourceChangedError("backup manifest checkpoint changed")
    source_manifest, source_manifest_hash = canonical_sqlite_manifest(
        backup_root / "mesa.db"
    )
    if source_manifest_hash != operation.get(
        "source_manifest_hash"
    ) or source_manifest != operation.get("source_manifest"):
        raise RebuildSourceChangedError("cutover source manifest changed")
    completed = int(operation.get("progress_completed", -1))
    total = int(operation.get("progress_total", -1))
    if completed < 0 or completed != total:
        raise RebuildPreparationError("cutover replay checkpoint is incomplete")

    generation = {
        "generation_id": target_generation_id,
        "vector_relative_path": (
            f"projection-generations/{target_generation_id}/vector.lance"
        ),
        "graph_relative_path": (
            f"projection-generations/{target_generation_id}/kuzu_db"
        ),
    }
    return RebuildPreparation(
        operation=operation,
        generation=generation,
        backup_root=backup_root,
        backup_manifest_hash=backup_manifest_hash,
        source_manifest=source_manifest,
        source_manifest_hash=source_manifest_hash,
        source_generation_id=source_generation_id,
        target_generation_id=target_generation_id,
        runtime_fencing_token=active.runtime_fencing_token,
    )
