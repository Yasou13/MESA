"""MIG-002/003 filesystem coordinator contracts without a live Kùzu engine."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from mesa_storage.kuzu_migration import (
    KuzuMigrationCoordinator,
    MigrationLockedError,
    MigrationResumeRequired,
)
from mesa_storage.kuzu_schema_migration import (
    ensure_schema_ready,
    migrate_schema_offline,
)
from mesa_storage.kuzu_setup import initialize_schema_artifact


def _marker_builder(counter: list[int]):
    def build(staging: Path) -> None:
        counter.append(1)
        staging.mkdir()
        (staging / "graph.marker").write_text("staged", encoding="utf-8")

    return build


def _validate(staging: Path) -> None:
    assert (staging / "graph.marker").read_text(encoding="utf-8") == "staged"


def test_promote_is_idempotent_and_retains_previous_live_directory(
    tmp_path: Path,
) -> None:
    live = tmp_path / "kuzu_db"
    live.mkdir()
    (live / "graph.marker").write_text("old", encoding="utf-8")
    coordinator = KuzuMigrationCoordinator(live, tmp_path / "migration-journal.db")
    builds: list[int] = []

    outcome = coordinator.run(
        migration_id="legacy-graph-v1",
        source_fingerprint="fixture-a",
        target_version="1",
        build_staging=_marker_builder(builds),
        validate_staging=_validate,
    )

    assert outcome.state == "PROMOTED"
    assert builds == [1]
    assert (live / "graph.marker").read_text(encoding="utf-8") == "staged"
    assert outcome.backup_path is not None
    assert (outcome.backup_path / "graph.marker").read_text(encoding="utf-8") == "old"

    repeated = coordinator.run(
        migration_id="legacy-graph-v1",
        source_fingerprint="fixture-a",
        target_version="1",
        build_staging=_marker_builder(builds),
        validate_staging=_validate,
    )
    assert repeated.state == "PROMOTED"
    assert builds == [1]


def test_lock_contention_is_fail_closed(tmp_path: Path) -> None:
    coordinator = KuzuMigrationCoordinator(
        tmp_path / "kuzu_db", tmp_path / "migration-journal.db"
    )
    builds: list[int] = []

    with coordinator.locked():
        with pytest.raises(MigrationLockedError):
            coordinator.run(
                migration_id="legacy-graph-v1",
                source_fingerprint="fixture-a",
                target_version="1",
                build_staging=_marker_builder(builds),
                validate_staging=_validate,
            )

    assert builds == []


def test_staged_migration_resumes_without_rebuilding_after_interruption(
    tmp_path: Path,
) -> None:
    coordinator = KuzuMigrationCoordinator(
        tmp_path / "kuzu_db", tmp_path / "migration-journal.db"
    )
    builds: list[int] = []

    def interrupted_validation(_staging: Path) -> None:
        raise MigrationResumeRequired("simulated interruption before validation")

    with pytest.raises(MigrationResumeRequired):
        coordinator.run(
            migration_id="legacy-graph-v1",
            source_fingerprint="fixture-a",
            target_version="1",
            build_staging=_marker_builder(builds),
            validate_staging=interrupted_validation,
        )

    resumed = coordinator.run(
        migration_id="legacy-graph-v1",
        source_fingerprint="fixture-a",
        target_version="1",
        build_staging=_marker_builder(builds),
        validate_staging=_validate,
    )
    assert resumed.state == "PROMOTED"
    assert builds == [1]


def test_rollback_restores_retained_previous_live_directory(tmp_path: Path) -> None:
    live = tmp_path / "kuzu_db"
    live.mkdir()
    (live / "graph.marker").write_text("old", encoding="utf-8")
    coordinator = KuzuMigrationCoordinator(live, tmp_path / "migration-journal.db")
    outcome = coordinator.run(
        migration_id="legacy-graph-v1",
        source_fingerprint="fixture-a",
        target_version="1",
        build_staging=_marker_builder([]),
        validate_staging=_validate,
    )

    restored = coordinator.rollback("legacy-graph-v1")
    assert restored.state == "ROLLED_BACK"
    assert (live / "graph.marker").read_text(encoding="utf-8") == "old"
    assert outcome.backup_path is not None
    assert outcome.backup_path.exists() is False


def test_cli_refuses_live_wipe_before_touching_any_database(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/migrate_to_kuzu.py",
            "--wipe",
            "--kuzu-db",
            str(tmp_path / "live-kuzu"),
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert "--wipe is refused" in result.stderr
    assert not (tmp_path / "live-kuzu").exists()


def test_real_kuzu_staging_database_is_promoted_after_validation(
    tmp_path: Path,
) -> None:
    kuzu = pytest.importorskip("kuzu")
    live = tmp_path / "kuzu_db"
    coordinator = KuzuMigrationCoordinator(live, tmp_path / "migration-journal.db")

    def build(staging: Path) -> None:
        database = kuzu.Database(str(staging))
        connection = kuzu.Connection(database)
        try:
            connection.execute("CREATE NODE TABLE Entity (id STRING, PRIMARY KEY (id))")
            connection.execute("CREATE (:Entity {id: 'node-1'})")
        finally:
            connection.close()
            database.close()

    def validate(staging: Path) -> None:
        database = kuzu.Database(str(staging))
        connection = kuzu.Connection(database)
        try:
            result = connection.execute("MATCH (n:Entity) RETURN count(n)")
            assert result.get_next()[0] == 1
        finally:
            connection.close()
            database.close()

    coordinator.run(
        migration_id="fixture-kuzu-v1",
        source_fingerprint="fixture-a",
        target_version="1",
        build_staging=build,
        validate_staging=validate,
    )
    validate(live)


def test_existing_kuzu_schema_is_rebuilt_offline_then_startup_is_journaled(
    tmp_path: Path,
) -> None:
    kuzu = pytest.importorskip("kuzu")
    live = tmp_path / "kuzu_db"
    initialize_schema_artifact(str(live))
    database = kuzu.Database(str(live))
    connection = kuzu.Connection(database)
    try:
        connection.execute(
            "CREATE (:Entity {id: 'node-1', name: 'Node', agent_id: 'agent-a', "
            "is_quarantined: false})"
        )
    finally:
        connection.close()
        database.close()

    with pytest.raises(RuntimeError, match="not version-journaled"):
        ensure_schema_ready(live)

    outcome = migrate_schema_offline(live)
    assert outcome.state == "PROMOTED"
    ensure_schema_ready(live)

    database = kuzu.Database(str(live))
    connection = kuzu.Connection(database)
    try:
        assert connection.execute("MATCH (n:Entity) RETURN count(n)").get_next()[0] == 1
    finally:
        connection.close()
        database.close()
