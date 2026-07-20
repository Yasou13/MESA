"""Crash-safe, manifest-backed backup and restore for an offline MESA storage root."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

MANIFEST_NAME = "mesa-backup-manifest.json"
MANIFEST_VERSION = 1
_SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
_FORBIDDEN_NAMES = {".env", "id_rsa", "id_ed25519"}
_FORBIDDEN_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}


class RecoveryError(RuntimeError):
    """An operator-correctable recovery safety failure."""


def _resolved_existing(path: Path) -> Path:
    if not path.exists():
        raise RecoveryError(f"path does not exist: {path}")
    return path.resolve(strict=True)


def _validate_path(path: Path, trusted_root: Path, *, must_exist: bool) -> Path:
    trusted = _resolved_existing(trusted_root)
    candidate = _resolved_existing(path) if must_exist else path.absolute().resolve(strict=False)
    if candidate == trusted or not candidate.is_relative_to(trusted):
        raise RecoveryError(f"path must be a child of trusted root {trusted}: {candidate}")
    current = candidate
    while current != trusted:
        if current.exists() and current.is_symlink():
            raise RecoveryError(f"symlink is not allowed in recovery path: {current}")
        current = current.parent
    return candidate


def _reject_sensitive_name(relative: Path) -> None:
    for part in relative.parts:
        lowered = part.lower()
        if lowered in _FORBIDDEN_NAMES or lowered.startswith(".env"):
            raise RecoveryError(f"secret-bearing path is not allowed in backup: {relative}")
        if Path(lowered).suffix in _FORBIDDEN_SUFFIXES:
            raise RecoveryError(f"key material is not allowed in backup: {relative}")


def _iter_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        _reject_sensitive_name(relative)
        if path.is_symlink():
            raise RecoveryError(f"symlink is not allowed in storage snapshot: {relative}")
        if path.is_file() and path.name != MANIFEST_NAME:
            yield path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sqlite_integrity(path: Path) -> str:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        result = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        connection.close()
        return result
    except sqlite3.DatabaseError as exc:
        raise RecoveryError(f"SQLite integrity check failed for {path.name}: {type(exc).__name__}") from exc


def _copy_sqlite(source: Path, destination: Path) -> None:
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
    except sqlite3.DatabaseError as exc:
        raise RecoveryError(f"SQLite snapshot failed for {source.name}: {type(exc).__name__}") from exc
    finally:
        destination_connection.close()
        source_connection.close()
    if _sqlite_integrity(destination) != "ok":
        raise RecoveryError(f"SQLite snapshot is corrupt: {source.name}")


def _copy_snapshot(source: Path, staging: Path) -> list[str]:
    sqlite_files: list[str] = []
    for source_file in _iter_files(source):
        relative = source_file.relative_to(source)
        if source_file.name.endswith(("-wal", "-shm")):
            continue
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source_file.suffix.lower() in _SQLITE_SUFFIXES:
            _copy_sqlite(source_file, destination)
            sqlite_files.append(relative.as_posix())
        else:
            shutil.copyfile(source_file, destination)
        shutil.copystat(source_file, destination, follow_symlinks=False)
        _fsync_file(destination)
    return sqlite_files


def _alembic_version(path: Path) -> str | None:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        connection.close()
        return str(row[0]) if row else None
    except sqlite3.DatabaseError:
        return None


def _write_manifest(staging: Path, *, source: Path, sqlite_files: list[str]) -> dict[str, Any]:
    files = [
        {
            "path": item.relative_to(staging).as_posix(),
            "sha256": _sha256(item),
            "size": item.stat().st_size,
        }
        for item in _iter_files(staging)
    ]
    sqlite = {
        relative: {
            "integrity_check": _sqlite_integrity(staging / relative),
            "alembic_version": _alembic_version(staging / relative),
        }
        for relative in sqlite_files
    }
    manifest: dict[str, Any] = {
        "format": "mesa-storage-backup",
        "manifest_version": MANIFEST_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "consistency_boundary": "offline-stores-stopped",
        "source_basename": source.name,
        "files": files,
        "sqlite": sqlite,
    }
    manifest_path = staging / MANIFEST_NAME
    with manifest_path.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    _fsync_dir(staging)
    return manifest


def _load_manifest(snapshot: Path) -> dict[str, Any]:
    manifest_path = snapshot / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryError("backup manifest is missing or invalid") from exc
    if manifest.get("format") != "mesa-storage-backup" or manifest.get("manifest_version") != MANIFEST_VERSION:
        raise RecoveryError("unsupported backup manifest")
    if manifest.get("consistency_boundary") != "offline-stores-stopped":
        raise RecoveryError("backup does not attest an offline consistency boundary")
    return manifest


def _verify_hashes(snapshot: Path, manifest: dict[str, Any]) -> None:
    expected: dict[str, dict[str, Any]] = {}
    for record in manifest.get("files", []):
        relative = Path(str(record.get("path", "")))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise RecoveryError("manifest contains an unsafe path")
        if relative.as_posix() in expected:
            raise RecoveryError("manifest contains a duplicate path")
        expected[relative.as_posix()] = record
    actual = {item.relative_to(snapshot).as_posix(): item for item in _iter_files(snapshot)}
    if set(expected) != set(actual):
        raise RecoveryError("backup file set does not match manifest")
    for relative, path in actual.items():
        record = expected[relative]
        if path.stat().st_size != int(record["size"]) or _sha256(path) != record["sha256"]:
            raise RecoveryError(f"backup hash mismatch: {relative}")


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _state_counts(connection: sqlite3.Connection, table: str) -> dict[str, int]:
    if not _table_exists(connection, table):
        return {}
    return {str(state): int(count) for state, count in connection.execute(
        f'SELECT state, COUNT(*) FROM "{table}" GROUP BY state'
    ).fetchall()}


def _reconcile_purge_ledger(connection: sqlite3.Connection) -> dict[str, int]:
    if not _table_exists(connection, "purge_journal"):
        return {"records": 0, "verified_targets": 0}
    records = connection.execute(
        "SELECT purge_id, agent_id, target_node_ids, state FROM purge_journal"
    ).fetchall()
    verified = 0
    protected_states = {
        "TOMBSTONED", "KUZU_APPLIED", "VECTOR_APPLIED", "VERIFIED", "FINALIZED",
        "RETRY_PENDING", "COMPENSATION_REQUIRED", "BLOCKED",
    }
    for purge_id, agent_id, raw_targets, state in records:
        try:
            targets = json.loads(raw_targets)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RecoveryError(f"purge journal has invalid target set: {purge_id}") from exc
        if not isinstance(targets, list) or any(not isinstance(item, str) for item in targets):
            raise RecoveryError(f"purge journal has invalid target set: {purge_id}")
        if state not in protected_states:
            continue
        for node_id in targets:
            row = connection.execute(
                "SELECT invalid_at, deleted_at, purge_id FROM nodes WHERE id = ? AND agent_id = ?",
                (node_id, agent_id),
            ).fetchone()
            if row is not None and (row[0] is None or row[1] is None or row[2] != purge_id):
                raise RecoveryError(f"purged node would be active after restore: {node_id}")
            verified += 1
    return {"records": len(records), "verified_targets": verified}


def validate_snapshot(snapshot: Path) -> dict[str, Any]:
    snapshot = snapshot.resolve(strict=True)
    manifest = _load_manifest(snapshot)
    _verify_hashes(snapshot, manifest)
    result: dict[str, Any] = {"valid": True, "files": len(manifest["files"]), "sqlite": {}}
    for relative in manifest.get("sqlite", {}):
        database = snapshot / relative
        if _sqlite_integrity(database) != "ok":
            raise RecoveryError(f"SQLite integrity check failed: {relative}")
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            result["sqlite"][relative] = {
                "integrity_check": "ok",
                "alembic_version": _alembic_version(database),
                "purge": _reconcile_purge_ledger(connection),
                "wal_states": _state_counts(connection, "lancedb_wal"),
                "dispatch_states": _state_counts(connection, "dispatch_queue"),
                "finalization_states": _state_counts(connection, "session_finalization_journal"),
            }
        finally:
            connection.close()
    result["completion_receipt_files"] = sorted(
        item["path"] for item in manifest["files"] if item["path"].endswith(".receipts.jsonl")
    )
    return result


def create_backup(source: Path, destination: Path, trusted_root: Path, *, stores_stopped: bool) -> dict[str, Any]:
    if not stores_stopped:
        raise RecoveryError("backup requires an explicit stores-stopped consistency boundary")
    source = _validate_path(source, trusted_root, must_exist=True)
    destination = _validate_path(destination, trusted_root, must_exist=False)
    if not source.is_dir():
        raise RecoveryError("source storage root must be a directory")
    if destination.exists():
        raise RecoveryError("backup destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.incomplete-", dir=destination.parent))
    try:
        sqlite_files = _copy_snapshot(source, staging)
        if not sqlite_files:
            raise RecoveryError("storage root contains no canonical SQLite database")
        _write_manifest(staging, source=source, sqlite_files=sqlite_files)
        validate_snapshot(staging)
        os.replace(staging, destination)
        _fsync_dir(destination.parent)
        return validate_snapshot(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def restore_backup(snapshot: Path, destination: Path, trusted_root: Path) -> dict[str, Any]:
    snapshot = _validate_path(snapshot, trusted_root, must_exist=True)
    destination = _validate_path(destination, trusted_root, must_exist=False)
    validation = validate_snapshot(snapshot)
    if destination.exists():
        raise RecoveryError("restore destination already exists; overwrite is forbidden")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.incomplete-", dir=destination.parent))
    try:
        for source_file in _iter_files(snapshot):
            relative = source_file.relative_to(snapshot)
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, target)
            shutil.copystat(source_file, target, follow_symlinks=False)
            _fsync_file(target)
        manifest_source = snapshot / MANIFEST_NAME
        manifest_target = staging / MANIFEST_NAME
        shutil.copyfile(manifest_source, manifest_target)
        _fsync_file(manifest_target)
        _fsync_dir(staging)
        validate_snapshot(staging)
        os.replace(staging, destination)
        _fsync_dir(destination.parent)
        return {"restored": True, "validation": validation, "destination": str(destination)}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mesa-recovery")
    parser.add_argument("--trusted-root", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup = subparsers.add_parser("backup")
    backup.add_argument("--source-root", type=Path, required=True)
    backup.add_argument("--backup-root", type=Path, required=True)
    backup.add_argument("--stores-stopped", action="store_true")
    restore = subparsers.add_parser("restore")
    restore.add_argument("--backup-root", type=Path, required=True)
    restore.add_argument("--restore-root", type=Path, required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--backup-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "backup":
            result = create_backup(args.source_root, args.backup_root, args.trusted_root, stores_stopped=args.stores_stopped)
        elif args.command == "restore":
            result = restore_backup(args.backup_root, args.restore_root, args.trusted_root)
        else:
            snapshot = _validate_path(args.backup_root, args.trusted_root, must_exist=True)
            result = validate_snapshot(snapshot)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except RecoveryError as exc:
        print(f"mesa-recovery: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
