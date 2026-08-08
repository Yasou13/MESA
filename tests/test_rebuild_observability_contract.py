"""Content-free rebuild health, metric and structured-log contracts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncIterator, cast
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
from alembic import command
from alembic.config import Config

from mesa_memory.observability.metrics import (
    PROM_V4_REBUILD_DURATION,
    PROM_V4_REBUILD_PARITY_MISSING,
    PROM_V4_REBUILD_PROGRESS_COMPLETED,
    PROM_V4_REBUILD_PROGRESS_TOTAL,
    PROM_V4_REBUILD_ROLLBACKS,
    PROM_V4_REBUILD_STAGING_BYTES,
    PROM_V4_REBUILD_STATE,
    update_v4_health_metrics,
)
from mesa_storage.rebuild_health import RebuildHealthReader
from mesa_storage.rebuild_observability import log_rebuild_event
from mesa_storage.repositories.operations import OperationRepository
from mesa_storage.sqlite_engine import AsyncEngine


class _Cursor:
    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    async def fetchone(self):  # type: ignore[no-untyped-def]
        return self._cursor.fetchone()

    async def fetchall(self):  # type: ignore[no-untyped-def]
        return self._cursor.fetchall()


class _Connection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    async def execute(self, statement, parameters=()):  # type: ignore[no-untyped-def]
        return _Cursor(self._connection.execute(statement, parameters))

    async def commit(self) -> None:
        self._connection.commit()


class _Engine:
    def __init__(self, database: Path) -> None:
        self._database = database

    def _open(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        connection = self._open()
        try:
            yield cast(aiosqlite.Connection, _Connection(connection))
        finally:
            connection.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        connection = self._open()
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield cast(aiosqlite.Connection, _Connection(connection))
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _config(database: Path) -> Config:
    config = Config(str(Path(__file__).parents[1] / "mesa_storage" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database}")
    return config


@pytest.mark.asyncio
async def test_durable_health_snapshot_has_no_operation_generation_or_path_ids(
    tmp_path: Path,
) -> None:
    database = tmp_path / "mesa.db"
    command.upgrade(_config(database), "head")
    engine = cast(AsyncEngine, _Engine(database))
    repository = OperationRepository(engine)
    reader = RebuildHealthReader(engine)

    assert await reader.snapshot() == {
        "status": "healthy",
        "state": "IDLE",
        "duration_seconds": 0,
        "progress": {"completed": 0, "total": 0},
        "parity_missing": 0,
        "staging_bytes": 0,
        "rollback_count": 0,
    }
    submitted = await repository.submit(
        requested_by_principal_id="admin-a",
        idempotency_key="rebuild-a",
        payload_hash=hashlib.sha256(b"rebuild").hexdigest(),
    )
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE system_operations SET created_at = datetime('now', '-10 seconds'), "
        "progress_completed = 4, progress_total = 10, checkpoint_json = ? "
        "WHERE operation_id = ?",
        (
            json.dumps(
                {
                    "parity": {"missing": 3},
                    "staging_bytes": 4096,
                    "rollback_count": 2,
                    "target_generation_id": "must-not-leak",
                }
            ),
            submitted["operation_id"],
        ),
    )
    connection.commit()
    connection.close()

    snapshot = await reader.snapshot()

    assert snapshot["status"] == "maintenance"
    assert snapshot["state"] == "PENDING"
    assert snapshot["duration_seconds"] >= 9
    assert snapshot["progress"] == {"completed": 4, "total": 10}
    assert snapshot["parity_missing"] == 3
    assert snapshot["staging_bytes"] == 4096
    assert snapshot["rollback_count"] == 2
    encoded = json.dumps(snapshot, sort_keys=True)
    assert submitted["operation_id"] not in encoded
    assert "must-not-leak" not in encoded
    assert str(tmp_path) not in encoded


def test_prometheus_rebuild_metrics_have_bounded_labels_and_durable_values() -> None:
    update_v4_health_metrics(
        {
            "v4_rebuild": {
                "state": "VERIFYING",
                "duration_seconds": 12,
                "progress": {"completed": 8, "total": 8},
                "parity_missing": 1,
                "staging_bytes": 2048,
                "rollback_count": 3,
            }
        }
    )

    assert PROM_V4_REBUILD_STATE.labels(state="VERIFYING")._value.get() == 1
    assert PROM_V4_REBUILD_STATE.labels(state="RUNNING")._value.get() == 0
    assert PROM_V4_REBUILD_DURATION._value.get() == 12
    assert PROM_V4_REBUILD_PROGRESS_COMPLETED._value.get() == 8
    assert PROM_V4_REBUILD_PROGRESS_TOTAL._value.get() == 8
    assert PROM_V4_REBUILD_PARITY_MISSING._value.get() == 1
    assert PROM_V4_REBUILD_STAGING_BYTES._value.get() == 2048
    assert PROM_V4_REBUILD_ROLLBACKS._value.get() == 3
    assert PROM_V4_REBUILD_STATE._labelnames == ("state",)


def test_rebuild_log_fields_replace_untrusted_identifiers_without_content() -> None:
    logger = SimpleNamespace(error=MagicMock())

    log_rebuild_event(
        "failed",
        operation_id="../../secret-operation",
        state="RETRYABLE_FAILED",
        generation="/private/vector.lance",
        error_class="failure\ncontent",
        progress_completed=2,
        progress_total=5,
        duration_seconds=1.23456,
        level="error",
        logger=logger,
    )

    logger.error.assert_called_once()
    event_name = logger.error.call_args.args[0]
    fields = logger.error.call_args.kwargs
    assert event_name == "v4_rebuild_operation"
    assert fields == {
        "rebuild_event": "failed",
        "operation_id": "invalid",
        "state": "RETRYABLE_FAILED",
        "generation": "invalid",
        "error_class": "invalid",
        "progress_completed": 2,
        "progress_total": 5,
        "duration_seconds": 1.235,
    }
    assert "secret" not in json.dumps(fields)
    assert "vector.lance" not in json.dumps(fields)


@pytest.mark.asyncio
async def test_public_health_reports_rebuild_maintenance_without_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mesa_memory.api import server

    health = {
        "sqlite": {"status": "healthy"},
        "vector": {"status": "healthy"},
        "graph": {"status": "healthy"},
        "v4_rebuild": {"status": "maintenance", "state": "RUNNING"},
    }
    monkeypatch.setattr(
        server.state,
        "dao",
        SimpleNamespace(health_check=AsyncMock(return_value=health)),
        raising=False,
    )

    response = await server.health_v3()

    assert response == {
        "status": "degraded",
        "maintenance": True,
        "rebuild_state": "RUNNING",
    }
