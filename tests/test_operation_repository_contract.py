"""Durable system-operation repository contracts."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncIterator, cast

import aiosqlite
import pytest
from alembic import command
from alembic.config import Config

from mesa_storage.dao import MemoryDAO
from mesa_storage.repositories.operations import (
    OperationActiveConflictError,
    OperationFencedError,
    OperationIdempotencyConflictError,
    OperationRepository,
    OperationRepositoryPort,
    OperationStateError,
)
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


class _SynchronousSQLiteEngine:
    """Local SQLite substitute that avoids thread scheduling in contract tests."""

    def __init__(self, database: Path) -> None:
        self.database = database

    def _open(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        raw = self._open()
        try:
            yield cast(aiosqlite.Connection, _Connection(raw))
        finally:
            raw.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        raw = self._open()
        raw.execute("BEGIN IMMEDIATE")
        try:
            yield cast(aiosqlite.Connection, _Connection(raw))
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()


def _config(database: Path) -> Config:
    config = Config(str(Path(__file__).parents[1] / "mesa_storage" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database}")
    return config


def _repository(tmp_path: Path) -> tuple[OperationRepository, Path, AsyncEngine]:
    database = tmp_path / "operations.sqlite"
    command.upgrade(_config(database), "head")
    engine = cast(AsyncEngine, _SynchronousSQLiteEngine(database))
    return OperationRepository(engine), database, engine


def _payload_hash(label: str = "default") -> str:
    return hashlib.sha256(label.encode()).hexdigest()


@pytest.mark.asyncio
async def test_submit_is_idempotent_and_rejects_payload_or_active_conflicts(
    tmp_path: Path,
) -> None:
    repository, database, _ = _repository(tmp_path)
    submitted = await repository.submit(
        requested_by_principal_id="admin-a",
        idempotency_key="rebuild-a",
        payload_hash=_payload_hash(),
    )
    duplicate = await repository.submit(
        requested_by_principal_id="admin-a",
        idempotency_key="rebuild-a",
        payload_hash=_payload_hash(),
    )

    assert duplicate["operation_id"] == submitted["operation_id"]
    with pytest.raises(OperationIdempotencyConflictError):
        await repository.submit(
            requested_by_principal_id="admin-a",
            idempotency_key="rebuild-a",
            payload_hash=_payload_hash("different"),
        )
    with pytest.raises(OperationActiveConflictError):
        await repository.submit(
            requested_by_principal_id="admin-a",
            idempotency_key="rebuild-b",
            payload_hash=_payload_hash("second"),
        )

    connection = sqlite3.connect(database)
    assert (
        connection.execute("SELECT count(*) FROM system_operations").fetchone()[0] == 1
    )
    assert (
        connection.execute("SELECT count(*) FROM system_operation_events").fetchone()[0]
        == 1
    )
    connection.close()


@pytest.mark.asyncio
async def test_submit_and_initial_event_are_one_transaction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository, database, _ = _repository(tmp_path)

    async def fail_event(*_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("event write failed")

    monkeypatch.setattr(OperationRepository, "_event", fail_event)
    with pytest.raises(RuntimeError, match="event write failed"):
        await repository.submit(
            requested_by_principal_id="admin-a",
            idempotency_key="rebuild-a",
            payload_hash=_payload_hash(),
        )

    connection = sqlite3.connect(database)
    assert (
        connection.execute("SELECT count(*) FROM system_operations").fetchone()[0] == 0
    )
    assert (
        connection.execute("SELECT count(*) FROM system_operation_events").fetchone()[0]
        == 0
    )
    connection.close()


@pytest.mark.asyncio
async def test_claim_renew_transition_and_checkpoint_are_fenced_and_audited(
    tmp_path: Path,
) -> None:
    repository, database, _ = _repository(tmp_path)
    submitted = await repository.submit(
        requested_by_principal_id="admin-a",
        idempotency_key="rebuild-a",
        payload_hash=_payload_hash(),
    )
    operation_id = submitted["operation_id"]
    claimed = await repository.claim(operation_id, runner_id="runner-a")
    await repository.renew(
        operation_id,
        runner_id="runner-a",
        claim_token=claimed["claim_token"],
        fencing_token=claimed["fencing_token"],
    )
    with pytest.raises(OperationFencedError):
        await repository.renew(
            operation_id,
            runner_id="runner-a",
            claim_token=claimed["claim_token"],
            fencing_token=claimed["fencing_token"] + 1,
        )

    running = await repository.transition(
        operation_id,
        to_state="RUNNING",
        runner_id="runner-a",
        claim_token=claimed["claim_token"],
        fencing_token=claimed["fencing_token"],
        progress_total=2,
        source_generation_id="legacy",
    )
    checkpointed = await repository.transition(
        operation_id,
        to_state="RUNNING",
        runner_id="runner-a",
        claim_token=claimed["claim_token"],
        fencing_token=claimed["fencing_token"],
        progress_completed=1,
        progress_total=2,
        checkpoint={"last_batch": 1},
    )
    assert running["state"] == "RUNNING"
    assert checkpointed["checkpoint"] == {"last_batch": 1}
    for state in ("VERIFYING", "READY_TO_CUTOVER", "COMPLETED"):
        completed = await repository.transition(
            operation_id,
            to_state=state,
            runner_id="runner-a",
            claim_token=claimed["claim_token"],
            fencing_token=claimed["fencing_token"],
            progress_completed=2,
            progress_total=2,
        )
    assert completed["state"] == "COMPLETED"
    assert completed["claim_token"] is None

    connection = sqlite3.connect(database)
    events = connection.execute(
        "SELECT sequence_number, event_type, to_state, checkpoint_hash "
        "FROM system_operation_events WHERE operation_id = ? "
        "ORDER BY sequence_number",
        (operation_id,),
    ).fetchall()
    connection.close()
    assert [row[0] for row in events] == list(range(1, 8))
    assert [row[1] for row in events] == [
        "SUBMITTED",
        "CLAIMED",
        "TRANSITIONED",
        "CHECKPOINTED",
        "TRANSITIONED",
        "TRANSITIONED",
        "TRANSITIONED",
    ]
    assert events[3][3] == _payload_hash('{"last_batch":1}')


@pytest.mark.asyncio
async def test_expired_lease_can_be_reclaimed_and_stale_runner_is_fenced(
    tmp_path: Path,
) -> None:
    repository, database, _ = _repository(tmp_path)
    submitted = await repository.submit(
        requested_by_principal_id="admin-a",
        idempotency_key="rebuild-a",
        payload_hash=_payload_hash(),
    )
    operation_id = submitted["operation_id"]
    first = await repository.claim(operation_id, runner_id="runner-a")
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE system_operations SET lease_expires_at = "
        "datetime('now', '-1 second') WHERE operation_id = ?",
        (operation_id,),
    )
    connection.commit()
    connection.close()

    second = await repository.claim(operation_id, runner_id="runner-b")

    assert second["fencing_token"] == first["fencing_token"] + 1
    assert second["attempt_count"] == 2
    with pytest.raises(OperationFencedError):
        await repository.transition(
            operation_id,
            to_state="RUNNING",
            runner_id="runner-a",
            claim_token=first["claim_token"],
            fencing_token=first["fencing_token"],
        )
    running = await repository.transition(
        operation_id,
        to_state="RUNNING",
        runner_id="runner-b",
        claim_token=second["claim_token"],
        fencing_token=second["fencing_token"],
    )
    assert running["state"] == "RUNNING"


@pytest.mark.asyncio
async def test_cancel_and_retry_preserve_the_same_operation_and_checkpoint(
    tmp_path: Path,
) -> None:
    repository, _, _ = _repository(tmp_path)
    first = await repository.submit(
        requested_by_principal_id="admin-a",
        idempotency_key="cancelled",
        payload_hash=_payload_hash("cancelled"),
    )
    assert (await repository.cancel(first["operation_id"]))["state"] == "CANCELLED"

    second = await repository.submit(
        requested_by_principal_id="admin-a",
        idempotency_key="retryable",
        payload_hash=_payload_hash("retryable"),
    )
    claimed = await repository.claim(second["operation_id"], runner_id="runner-a")
    failed = await repository.transition(
        second["operation_id"],
        to_state="RETRYABLE_FAILED",
        runner_id="runner-a",
        claim_token=claimed["claim_token"],
        fencing_token=claimed["fencing_token"],
        progress_total=2,
        checkpoint={"last_batch": 1},
        error_class="VectorProviderUnavailable",
        error_code="PROVIDER_UNAVAILABLE",
    )
    retried = await repository.retry(second["operation_id"])

    assert retried["operation_id"] == failed["operation_id"]
    assert retried["state"] == "PENDING"
    assert retried["checkpoint"] == {"last_batch": 1}
    with pytest.raises(OperationStateError):
        await repository.retry(second["operation_id"])
    assert (await repository.cancel(second["operation_id"]))["state"] == "CANCELLED"


@pytest.mark.asyncio
async def test_memory_dao_is_only_a_compatibility_delegate(tmp_path: Path) -> None:
    _, _, engine = _repository(tmp_path)
    dao = MemoryDAO(engine, SimpleNamespace())
    port: OperationRepositoryPort = dao.operations

    submitted = await dao.submit_system_operation(
        requested_by_principal_id="admin-a",
        idempotency_key="rebuild-a",
        payload_hash=_payload_hash(),
    )

    assert isinstance(port, OperationRepository)
    assert (await dao.get_system_operation(submitted["operation_id"]))[
        "operation_id"
    ] == submitted["operation_id"]
