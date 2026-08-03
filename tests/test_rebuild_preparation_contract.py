"""Offline rebuild preparation and checkpoint safety contracts."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config

from mesa_storage.rebuild_preparation import (
    OfflineRebuildPreparer,
    RebuildBacklogError,
    RebuildDiskCapacityError,
    RebuildPathError,
    inspect_rebuild_preflight,
)
from mesa_storage.recovery import validate_snapshot
from mesa_storage.writer_lock import StorageWriterLock

_OPERATION_ID = "11111111-2222-4333-8444-555555555555"


def _config(database: Path) -> Config:
    config = Config(str(Path(__file__).parents[1] / "mesa_storage" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database}")
    return config


def _storage(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    trusted = tmp_path / "trusted"
    storage = trusted / "storage"
    work = trusted / "work"
    storage.mkdir(parents=True)
    work.mkdir()
    database = storage / "mesa.db"
    command.upgrade(_config(database), "head")
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO system_operations (operation_id, operation_kind, scope_kind, "
        "scope_key, requested_by_principal_id, idempotency_key, payload_hash, "
        "state, claimed_by, claim_token, fencing_token, attempt_count, "
        "lease_expires_at) VALUES (?, 'PROJECTION_REBUILD', 'STORAGE_ROOT', "
        "'default', 'admin-a', 'rebuild-a', ?, 'CLAIMED', 'runner-a', "
        "'claim-a', 1, 1, datetime('now', '+1 hour'))",
        (_OPERATION_ID, "a" * 64),
    )
    connection.commit()
    connection.close()
    return trusted, storage, work, database


def _inspect(
    trusted: Path,
    storage: Path,
    work: Path,
    writer_lock: StorageWriterLock,
    **changes: object,
):
    arguments = {
        "trusted_root": trusted,
        "storage_root": storage,
        "work_root": work,
        "operation_id": _OPERATION_ID,
        "runner_id": "runner-a",
        "claim_token": "claim-a",
        "operation_fencing_token": 1,
        "writer_lock": writer_lock,
    }
    arguments.update(changes)
    return inspect_rebuild_preflight(**arguments)  # type: ignore[arg-type]


def test_preflight_requires_drain_capacity_and_safe_nonoverlapping_paths(
    tmp_path: Path,
) -> None:
    trusted, storage, work, database = _storage(tmp_path)
    with StorageWriterLock.acquire(storage, owner="rebuild-runner") as writer_lock:
        preflight = _inspect(trusted, storage, work, writer_lock)
        assert preflight.source_generation_id == "legacy"
        assert preflight.source_manifest["alembic_head"] == "fc3d4e5f6a7b"

        connection = sqlite3.connect(database)
        connection.execute(
            "INSERT INTO raw_logs (agent_id, payload, status) "
            "VALUES ('agent-a', '{}', 'DEFERRED')"
        )
        connection.commit()
        connection.close()
        with pytest.raises(RebuildBacklogError, match="backlog"):
            _inspect(trusted, storage, work, writer_lock)

        connection = sqlite3.connect(database)
        connection.execute("UPDATE raw_logs SET status = 'CONSOLIDATED'")
        connection.commit()
        connection.close()
        with pytest.raises(RebuildDiskCapacityError, match="free space"):
            _inspect(
                trusted,
                storage,
                work,
                writer_lock,
                disk_usage=lambda _path: SimpleNamespace(free=0),
            )
        with pytest.raises(RebuildPathError, match="overlap"):
            _inspect(trusted, storage, storage, writer_lock)


def test_source_manifest_ignores_operation_progress_but_detects_canonical_change(
    tmp_path: Path,
) -> None:
    trusted, storage, work, database = _storage(tmp_path)
    with StorageWriterLock.acquire(storage, owner="rebuild-runner") as writer_lock:
        original = _inspect(trusted, storage, work, writer_lock)
        connection = sqlite3.connect(database)
        connection.execute(
            "UPDATE system_operations SET progress_completed = 0, "
            "updated_at = CURRENT_TIMESTAMP WHERE operation_id = ?",
            (_OPERATION_ID,),
        )
        connection.commit()
        connection.close()
        administrative = _inspect(trusted, storage, work, writer_lock)
        assert administrative.source_manifest_hash == original.source_manifest_hash

        connection = sqlite3.connect(database)
        connection.execute(
            "INSERT INTO system_config (key, value) VALUES ('rebuild-test', 'changed')"
        )
        connection.commit()
        connection.close()
        changed = _inspect(trusted, storage, work, writer_lock)
        assert changed.source_manifest_hash != original.source_manifest_hash


@pytest.mark.asyncio
async def test_preparer_creates_valid_backup_generation_and_fenced_checkpoint(
    tmp_path: Path,
) -> None:
    trusted, storage, work, database = _storage(tmp_path)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT * FROM system_operations WHERE operation_id = ?", (_OPERATION_ID,)
    ).fetchone()
    connection.close()
    assert row is not None
    operation = dict(row)
    operation["checkpoint"] = {}
    running = {**operation, "state": "RUNNING"}
    prepared = {
        **running,
        "checkpoint": {"phase": "PREPARED"},
        "target_generation_id": f"rebuild-{_OPERATION_ID}",
    }
    operations = SimpleNamespace(transition=AsyncMock(side_effect=[running, prepared]))
    generations = SimpleNamespace(
        create_staging=AsyncMock(
            return_value={
                "generation_id": f"rebuild-{_OPERATION_ID}",
                "lifecycle_state": "STAGING",
            }
        )
    )
    preparer = OfflineRebuildPreparer(operations, generations)  # type: ignore[arg-type]

    with StorageWriterLock.acquire(storage, owner="rebuild-runner") as writer_lock:
        result = await preparer.prepare(
            trusted_root=trusted,
            storage_root=storage,
            work_root=work,
            operation=operation,
            runner_id="runner-a",
            writer_lock=writer_lock,
            provider_manifest={
                "embedding_provider": "local-test",
                "embedding_version": "v1",
                "dimension": 8,
            },
        )

    assert validate_snapshot(result.backup_root)["valid"] is True
    assert result.target_generation_id == f"rebuild-{_OPERATION_ID}"
    assert len(result.backup_manifest_hash) == 64
    assert operations.transition.await_count == 2
    generations.create_staging.assert_awaited_once()
    final_checkpoint = operations.transition.await_args.kwargs["checkpoint"]
    assert final_checkpoint["phase"] == "PREPARED"
    assert "backup_root" not in final_checkpoint
