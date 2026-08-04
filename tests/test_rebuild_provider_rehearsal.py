"""Disposable real-provider backup, rebuild, parity and rollback rehearsal."""

from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, cast

import aiosqlite
import pytest
from alembic import command
from alembic.config import Config

from mesa_storage import kuzu_setup
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.projection_generations import ProjectionGenerationRepository
from mesa_storage.rebuild_cutover import (
    ParityGatedActivator,
    ProjectionParityVerifier,
    RebuildVerificationError,
    default_graph_verification_factory,
    default_vector_verification_factory,
)
from mesa_storage.rebuild_preparation import (
    OfflineRebuildPreparer,
    canonical_sqlite_manifest,
)
from mesa_storage.rebuild_replay import ProjectionReplayer, ProjectionSnapshot
from mesa_storage.recovery import validate_snapshot
from mesa_storage.repositories.operations import OperationRepository
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine
from mesa_storage.writer_lock import StorageWriterLock


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


def _seed_canonical_source(database: Path) -> None:
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO v4_entities (entity_id, tenant_id, entity_type, "
        "canonical_name, normalized_name, identity_key) VALUES "
        "('entity-1', 'tenant-a', 'concept', 'Alpha', 'alpha', 'entity-1')"
    )
    connection.execute(
        "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, "
        "agent_id, state) VALUES "
        "('pipeline-1', 'tenant-a', 'session-a', 'agent-a', 'COMMITTED')"
    )
    connection.execute(
        "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, "
        "agent_id, session_id, content_payload, pipeline_run_id, "
        "embedding_provider, embedding_model, embedding_version, "
        "embedding_dimension, state) VALUES "
        "('mutation-1', 'candidate-1', 'tenant-a', 'agent-a', 'session-a', "
        "'source', 'pipeline-1', 'deterministic-test', 'embed-model', 'v1', 3, "
        "'COMMITTED')"
    )
    connection.execute(
        "INSERT INTO v4_assertions (assertion_id, tenant_id, dataset_id, "
        "subject_id, predicate, literal_value, source_ref, document_id, "
        "revision_id, chunk_id, evidence_span, confidence, status, mutation_id, "
        "pipeline_run_id) VALUES ('assertion-1', 'tenant-a', 'dataset-a', "
        "'entity-1', 'RELATES_TO', 'literal-1', 'source-a', 'document-a', "
        "'revision-1', 'chunk-1', '0:4', 0.9, 'ACTIVE', 'mutation-1', "
        "'pipeline-1')"
    )
    artifacts = (
        ("vector-1", "VECTOR", "ENTITY_VECTOR", "entity-1"),
        ("graph-e1", "GRAPH", "ENTITY", "entity-1"),
        ("graph-a1", "GRAPH", "ASSERTION", "assertion-1"),
    )
    for registry_id, store, kind, artifact_id in artifacts:
        connection.execute(
            "INSERT INTO artifact_registry (registry_id, tenant_id, agent_id, "
            "dataset_id, store_name, artifact_kind, physical_artifact_id, state) "
            "VALUES (?, 'tenant-a', 'agent-a', 'dataset-a', ?, ?, ?, 'ACTIVE')",
            (registry_id, store, kind, artifact_id),
        )
        connection.execute(
            "INSERT INTO artifact_sources (source_ownership_id, registry_id, "
            "mutation_id, pipeline_run_id, dataset_id, source_ref, state) "
            "VALUES (?, ?, 'mutation-1', 'pipeline-1', 'dataset-a', 'source-a', "
            "'ACTIVE')",
            (f"source-{registry_id}", registry_id),
        )
    connection.commit()
    connection.close()


async def _embedding(text: str) -> list[float]:
    return [float(len(text)), 0.5, 1.0]


async def _seed_real_projection(database: Path, storage: Path) -> None:
    snapshot = ProjectionSnapshot(database)
    vector = VectorEngine(str(storage / "vector.lance"), embedding_provider=_embedding)
    graph_path = storage / "kuzu_db"
    kuzu_setup.initialize_schema_artifact(str(graph_path))
    graph = KuzuGraphProvider(str(graph_path))
    await vector.initialize()
    await graph.initialize()
    try:
        for lane in ("vector", "graph_entity", "graph_assertion", "graph_link"):
            rows = snapshot.fetch(lane, offset=0, limit=100)
            if rows:
                await ProjectionReplayer._apply_batch(
                    lane,
                    rows,
                    vector=vector,
                    graph=graph,
                    expected_provider=("embed-model", "v1", 3),
                )
    finally:
        await graph.close()
        await vector.close()


class _FailAfterActivatedParity:
    def __init__(self) -> None:
        self._verifier = ProjectionParityVerifier()
        self.calls = 0

    async def verify(self, **kwargs):  # type: ignore[no-untyped-def]
        report = await self._verifier.verify(**kwargs)
        self.calls += 1
        if self.calls == 2:
            raise RebuildVerificationError("injected post-cutover health failure")
        return report


def _require_real_provider_runtime(tmp_path: Path) -> None:
    probe = (
        "import lancedb, pathlib, sys; "
        "pathlib.Path(sys.argv[1]).mkdir(); lancedb.connect(sys.argv[1])"
    )
    try:
        subprocess.run(
            [sys.executable, "-c", probe, str(tmp_path / "lancedb-probe")],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            "local LanceDB provider initialization timed out",
            pytrace=False,
        )
    except subprocess.CalledProcessError as exc:
        pytest.fail(
            f"local LanceDB provider probe failed with exit code {exc.returncode}",
            pytrace=False,
        )


@pytest.mark.parametrize(
    "probe_failure",
    [
        subprocess.TimeoutExpired(cmd="lancedb-probe", timeout=10),
        subprocess.CalledProcessError(returncode=1, cmd="lancedb-probe"),
    ],
)
def test_installed_provider_probe_failure_fails_rehearsal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_failure: Exception,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(probe_failure),
    )

    try:
        _require_real_provider_runtime(tmp_path)
    except BaseException as exc:
        assert isinstance(exc, pytest.fail.Exception)
    else:
        pytest.fail("failed provider probe unexpectedly passed")


def test_migration_dr_requires_real_provider_rehearsal() -> None:
    workflow = (Path(__file__).parents[1] / ".github/workflows/ci.yml").read_text()
    migration_job = workflow.split("  migration-dr:", maxsplit=1)[1].split(
        "\n  package:", maxsplit=1
    )[0]

    assert 'MESA_REQUIRE_REAL_PROVIDER_REHEARSAL: "true"' in migration_job


@pytest.mark.asyncio
async def test_real_provider_backup_rebuild_reopen_parity_and_rollback(
    tmp_path: Path,
) -> None:
    _require_real_provider_runtime(tmp_path)
    trusted = tmp_path / "trusted"
    storage = trusted / "storage"
    work = trusted / "work"
    storage.mkdir(parents=True)
    work.mkdir()
    database = storage / "mesa.db"
    command.upgrade(_config(database), "head")
    _seed_canonical_source(database)
    await _seed_real_projection(database, storage)

    engine = cast(AsyncEngine, _Engine(database))
    operations = OperationRepository(engine)
    generations = ProjectionGenerationRepository(engine)
    submitted = await operations.submit(
        requested_by_principal_id="admin-a",
        idempotency_key="real-provider-rehearsal",
        payload_hash=hashlib.sha256(b"storage-root").hexdigest(),
    )
    claimed = await operations.claim(
        submitted["operation_id"], runner_id="runner-a", lease_seconds=3600
    )
    provider_manifest = {
        "embedding_provider": "deterministic-test",
        "embedding_model": "embed-model",
        "embedding_version": "v1",
        "dimension": 3,
    }
    vector_factory = default_vector_verification_factory(
        embedding_provider=_embedding,
        allow_model_loading=False,
    )
    source_hash = canonical_sqlite_manifest(database)[1]

    with StorageWriterLock.acquire(storage, owner="rebuild-rehearsal") as writer_lock:
        preparation = await OfflineRebuildPreparer(operations, generations).prepare(
            trusted_root=trusted,
            storage_root=storage,
            work_root=work,
            operation=claimed,
            runner_id="runner-a",
            writer_lock=writer_lock,
            provider_manifest=provider_manifest,
        )
        assert validate_snapshot(preparation.backup_root)["valid"] is True
        replay = await ProjectionReplayer(operations).replay(
            preparation=preparation,
            trusted_root=trusted,
            storage_root=storage,
            runner_id="runner-a",
            provider_manifest=provider_manifest,
            batch_size=1,
            lease_seconds=3600,
            embedding_provider=_embedding,
        )
        verifier = _FailAfterActivatedParity()
        with pytest.raises(RebuildVerificationError, match="rolled back"):
            await ParityGatedActivator(
                operations,
                generations,
                verifier,  # type: ignore[arg-type]
            ).activate(
                preparation=preparation,
                replay=replay,
                trusted_root=trusted,
                storage_root=storage,
                runner_id="runner-a",
                vector_factory=vector_factory,
                graph_factory=default_graph_verification_factory,
                lease_seconds=3600,
            )

    active = await generations.resolve_active(
        storage_root=storage, trusted_root=trusted
    )
    operation = await operations.get(submitted["operation_id"])
    assert active.generation_id == "legacy"
    assert operation is not None and operation["state"] == "RETRYABLE_FAILED"
    assert operation["checkpoint"]["rollback_count"] == 1
    assert verifier.calls == 3
    assert preparation.backup_root.exists()
    assert (
        storage
        / "projection-generations"
        / preparation.target_generation_id
        / "vector.lance"
    ).exists()
    retained_report = await ProjectionParityVerifier().verify(
        snapshot=ProjectionSnapshot(preparation.backup_root / "mesa.db"),
        paths=active,
        vector_factory=vector_factory,
        graph_factory=default_graph_verification_factory,
    )
    assert retained_report.passed is True
    assert canonical_sqlite_manifest(database)[1] == source_hash
