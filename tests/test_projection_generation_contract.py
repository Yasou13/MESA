"""Projection generation resolution, activation and rollback contracts."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, cast

import aiosqlite
import pytest
from alembic import command
from alembic.config import Config

from mesa_storage.projection_generations import (
    ProjectionGenerationConflictError,
    ProjectionGenerationFencedError,
    ProjectionGenerationRepository,
    ProjectionPathError,
)
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


class _SynchronousSQLiteEngine:
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


def _repositories(
    tmp_path: Path,
) -> tuple[
    ProjectionGenerationRepository,
    OperationRepository,
    Path,
    Path,
]:
    storage = tmp_path / "storage"
    storage.mkdir()
    database = storage / "mesa.db"
    command.upgrade(_config(database), "head")
    engine = cast(AsyncEngine, _SynchronousSQLiteEngine(database))
    return (
        ProjectionGenerationRepository(engine),
        OperationRepository(engine),
        storage,
        database,
    )


async def _running_operation(
    operations: OperationRepository,
) -> tuple[str, dict]:
    submitted = await operations.submit(
        requested_by_principal_id="admin-a",
        idempotency_key="rebuild-a",
        payload_hash=hashlib.sha256(b"projection-rebuild").hexdigest(),
    )
    claimed = await operations.claim(submitted["operation_id"], runner_id="runner-a")
    await operations.transition(
        submitted["operation_id"],
        to_state="RUNNING",
        runner_id="runner-a",
        claim_token=claimed["claim_token"],
        fencing_token=claimed["fencing_token"],
    )
    return submitted["operation_id"], claimed


@pytest.mark.asyncio
async def test_active_generation_paths_are_relative_and_symlink_safe(
    tmp_path: Path,
) -> None:
    generations, _, storage, _ = _repositories(tmp_path)
    active = await generations.resolve_active(
        storage_root=storage, trusted_root=tmp_path
    )

    assert active.generation_id == "legacy"
    assert active.vector_path == storage / "vector.lance"
    assert active.graph_path == storage / "kuzu_db"

    outside = tmp_path / "outside"
    outside.mkdir()
    active.vector_path.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProjectionPathError, match="symlink"):
        await generations.resolve_active(storage_root=storage, trusted_root=tmp_path)

    unrelated_trust_root = tmp_path / "unrelated"
    unrelated_trust_root.mkdir()
    with pytest.raises(ProjectionPathError, match="trusted root"):
        await generations.resolve_active(
            storage_root=storage, trusted_root=unrelated_trust_root
        )


@pytest.mark.asyncio
async def test_staging_generation_requires_current_operation_fence(
    tmp_path: Path,
) -> None:
    generations, operations, _, _ = _repositories(tmp_path)
    operation_id, claimed = await _running_operation(operations)

    with pytest.raises(ProjectionGenerationFencedError):
        await generations.create_staging(
            operation_id=operation_id,
            generation_id="generation-a",
            runner_id="runner-a",
            claim_token=claimed["claim_token"],
            operation_fencing_token=claimed["fencing_token"] + 1,
        )

    created = await generations.create_staging(
        operation_id=operation_id,
        generation_id="generation-a",
        runner_id="runner-a",
        claim_token=claimed["claim_token"],
        operation_fencing_token=claimed["fencing_token"],
        provider_manifest={"embedding_provider": "test", "dimension": 8},
    )
    duplicate = await generations.create_staging(
        operation_id=operation_id,
        generation_id="generation-a",
        runner_id="runner-a",
        claim_token=claimed["claim_token"],
        operation_fencing_token=claimed["fencing_token"],
        provider_manifest={"embedding_provider": "test", "dimension": 8},
    )

    assert created["lifecycle_state"] == "STAGING"
    assert created["vector_relative_path"] == (
        "projection-generations/generation-a/vector.lance"
    )
    assert duplicate["generation_id"] == created["generation_id"]


async def _ready_generation(
    generations: ProjectionGenerationRepository,
    operations: OperationRepository,
) -> tuple[str, dict]:
    operation_id, claimed = await _running_operation(operations)
    await generations.create_staging(
        operation_id=operation_id,
        generation_id="generation-a",
        runner_id="runner-a",
        claim_token=claimed["claim_token"],
        operation_fencing_token=claimed["fencing_token"],
    )
    await operations.transition(
        operation_id,
        to_state="RUNNING",
        runner_id="runner-a",
        claim_token=claimed["claim_token"],
        fencing_token=claimed["fencing_token"],
        checkpoint={"generation_created": True},
        target_generation_id="generation-a",
        source_generation_id="legacy",
    )
    await operations.transition(
        operation_id,
        to_state="VERIFYING",
        runner_id="runner-a",
        claim_token=claimed["claim_token"],
        fencing_token=claimed["fencing_token"],
    )
    await operations.transition(
        operation_id,
        to_state="READY_TO_CUTOVER",
        runner_id="runner-a",
        claim_token=claimed["claim_token"],
        fencing_token=claimed["fencing_token"],
    )
    return operation_id, claimed


@pytest.mark.asyncio
async def test_activation_is_atomic_and_rollback_preserves_both_generations(
    tmp_path: Path,
) -> None:
    generations, operations, storage, database = _repositories(tmp_path)
    operation_id, claimed = await _ready_generation(generations, operations)
    before = await generations.resolve_active(
        storage_root=storage, trusted_root=tmp_path
    )
    connection = sqlite3.connect(database)
    connection.execute("""CREATE TRIGGER fail_projection_activation
        BEFORE UPDATE ON projection_runtime
        BEGIN
            SELECT RAISE(ABORT, 'activation fault');
        END""")
    connection.commit()
    connection.close()

    with pytest.raises(sqlite3.IntegrityError, match="activation fault"):
        await generations.activate(
            "generation-a",
            operation_id=operation_id,
            runner_id="runner-a",
            claim_token=claimed["claim_token"],
            operation_fencing_token=claimed["fencing_token"],
            expected_active_generation_id="legacy",
            runtime_fencing_token=before.runtime_fencing_token,
        )

    connection = sqlite3.connect(database)
    states_after_fault = dict(
        connection.execute(
            "SELECT generation_id, lifecycle_state FROM projection_generations"
        ).fetchall()
    )
    assert states_after_fault == {"legacy": "ACTIVE", "generation-a": "STAGING"}
    connection.execute("DROP TRIGGER fail_projection_activation")
    connection.commit()
    connection.close()

    activated = await generations.activate(
        "generation-a",
        operation_id=operation_id,
        runner_id="runner-a",
        claim_token=claimed["claim_token"],
        operation_fencing_token=claimed["fencing_token"],
        expected_active_generation_id="legacy",
        runtime_fencing_token=before.runtime_fencing_token,
    )
    assert activated["active_generation_id"] == "generation-a"
    assert activated["previous_generation_id"] == "legacy"
    assert activated["fencing_token"] == before.runtime_fencing_token + 1

    with pytest.raises(ProjectionGenerationFencedError):
        await generations.activate(
            "generation-a",
            operation_id=operation_id,
            runner_id="runner-a",
            claim_token=claimed["claim_token"],
            operation_fencing_token=claimed["fencing_token"],
            expected_active_generation_id="legacy",
            runtime_fencing_token=before.runtime_fencing_token,
        )

    rolled_back = await generations.rollback(
        operation_id=operation_id,
        runner_id="runner-a",
        claim_token=claimed["claim_token"],
        operation_fencing_token=claimed["fencing_token"],
        expected_active_generation_id="generation-a",
        runtime_fencing_token=activated["fencing_token"],
    )
    assert rolled_back["active_generation_id"] == "legacy"
    assert rolled_back["previous_generation_id"] is None

    connection = sqlite3.connect(database)
    states_after_rollback = dict(
        connection.execute(
            "SELECT generation_id, lifecycle_state FROM projection_generations"
        ).fetchall()
    )
    connection.close()
    assert states_after_rollback == {"legacy": "ACTIVE", "generation-a": "FAILED"}
    with pytest.raises(ProjectionGenerationConflictError):
        await generations.rollback(
            operation_id=operation_id,
            runner_id="runner-a",
            claim_token=claimed["claim_token"],
            operation_fencing_token=claimed["fencing_token"],
            expected_active_generation_id="legacy",
            runtime_fencing_token=rolled_back["fencing_token"],
        )
