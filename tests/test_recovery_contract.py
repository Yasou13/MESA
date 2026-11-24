from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from mesa_storage.recovery import (
    RecoveryError,
    create_backup,
    restore_backup,
    validate_snapshot,
)


def _synthetic_storage(root: Path) -> Path:
    source = root / "source"
    source.mkdir()
    database = source / "mesa.db"
    connection = sqlite3.connect(database)
    connection.executescript("""
        CREATE TABLE alembic_version (version_num TEXT NOT NULL);
        INSERT INTO alembic_version VALUES ('f9c0d1e2f3a4');
        CREATE TABLE documents (
            document_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE document_revisions (
            revision_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE source_chunks (
            chunk_id TEXT PRIMARY KEY,
            revision_id TEXT NOT NULL,
            content_payload TEXT NOT NULL
        );
        CREATE TABLE v4_assertions (
            assertion_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE projection_outbox (state TEXT NOT NULL);
        CREATE TABLE artifact_cleanup_outbox (state TEXT NOT NULL);
        CREATE TABLE dispatch_queue (state TEXT NOT NULL);
        CREATE TABLE session_finalization_journal (state TEXT NOT NULL);
        INSERT INTO documents VALUES ('doc-purged', 'agent-a', 'PURGED');
        INSERT INTO documents VALUES ('doc-active', 'agent-a', 'ACTIVE');
        INSERT INTO document_revisions VALUES ('rev-purged', 'doc-purged', 'PURGED');
        INSERT INTO document_revisions VALUES ('rev-active', 'doc-active', 'ACTIVE');
        INSERT INTO source_chunks VALUES ('chunk-purged', 'rev-purged', '');
        INSERT INTO source_chunks VALUES ('chunk-active', 'rev-active', 'active content');
        INSERT INTO v4_assertions VALUES ('assert-purged', 'doc-purged', 'PURGED');
        INSERT INTO v4_assertions VALUES ('assert-active', 'doc-active', 'ACTIVE');
        INSERT INTO projection_outbox VALUES ('COMPLETED');
        INSERT INTO artifact_cleanup_outbox VALUES ('RETRY_PENDING');
        INSERT INTO dispatch_queue VALUES ('COMPLETED');
        INSERT INTO session_finalization_journal VALUES ('RETRY_PENDING');
        """)
    connection.commit()
    connection.close()
    (source / "vector.lance").mkdir()
    (source / "vector.lance" / "data.bin").write_bytes(b"vector")
    (source / "dlq.jsonl").write_text('{"id":"record-1"}\n', encoding="utf-8")
    (source / "dlq.jsonl.receipts.jsonl").write_text(
        '{"id":"record-1","state":"COMPLETED"}\n', encoding="utf-8"
    )
    return source


def test_manifest_backup_restore_preserves_durable_state(tmp_path: Path) -> None:
    source = _synthetic_storage(tmp_path)
    backup = tmp_path / "backups" / "snapshot-1"
    restore = tmp_path / "restore" / "clean"

    created = create_backup(source, backup, tmp_path, stores_stopped=True)
    assert created["valid"] is True
    assert created["sqlite"]["mesa.db"]["purge"] == {
        "purged_documents": 1,
        "verified_documents": 1,
    }
    assert created["sqlite"]["mesa.db"]["projection_states"] == {"COMPLETED": 1}
    assert created["sqlite"]["mesa.db"]["cleanup_states"] == {"RETRY_PENDING": 1}
    assert created["completion_receipt_files"] == ["dlq.jsonl.receipts.jsonl"]

    restored = restore_backup(backup, restore, tmp_path)
    assert restored["restored"] is True
    result = validate_snapshot(restore)
    assert result["sqlite"]["mesa.db"]["dispatch_states"] == {"COMPLETED": 1}
    connection = sqlite3.connect(restore / "mesa.db")
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM documents WHERE status = 'ACTIVE'"
        ).fetchone()[0]
        == 1
    )
    connection.close()
    assert (restore / "vector.lance" / "data.bin").read_bytes() == b"vector"


def test_backup_requires_offline_boundary_and_safe_roots(tmp_path: Path) -> None:
    source = _synthetic_storage(tmp_path)
    with pytest.raises(RecoveryError, match="stores-stopped"):
        create_backup(source, tmp_path / "backup", tmp_path, stores_stopped=False)
    with pytest.raises(RecoveryError, match="child of trusted root"):
        create_backup(source, tmp_path.parent / "escape", tmp_path, stores_stopped=True)


def test_hash_tamper_and_purge_resurrection_fail_closed(tmp_path: Path) -> None:
    source = _synthetic_storage(tmp_path)
    backup = tmp_path / "backup"
    create_backup(source, backup, tmp_path, stores_stopped=True)
    (backup / "dlq.jsonl").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(RecoveryError, match="hash mismatch"):
        validate_snapshot(backup)

    backup = tmp_path / "backup-2"
    create_backup(source, backup, tmp_path, stores_stopped=True)
    database = backup / "mesa.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE v4_assertions SET status = 'ACTIVE' WHERE assertion_id = 'assert-purged'"
    )
    connection.commit()
    connection.close()
    manifest_path = backup / "mesa-backup-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for record in manifest["files"]:
        if record["path"] == "mesa.db":
            import hashlib

            record["sha256"] = hashlib.sha256(database.read_bytes()).hexdigest()
            record["size"] = database.stat().st_size
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RecoveryError, match="would be active"):
        validate_snapshot(backup)


def test_restore_never_overwrites_existing_target(tmp_path: Path) -> None:
    source = _synthetic_storage(tmp_path)
    backup = tmp_path / "backup"
    create_backup(source, backup, tmp_path, stores_stopped=True)
    destination = tmp_path / "restore"
    destination.mkdir()
    (destination / "owner.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(RecoveryError, match="already exists"):
        restore_backup(backup, destination, tmp_path)
    assert (destination / "owner.txt").read_text(encoding="utf-8") == "preserve"


@pytest.mark.asyncio
async def test_real_lancedb_reopen_after_restore(tmp_path: Path) -> None:
    from mesa_storage.vector_engine import VectorEngine

    source = _synthetic_storage(tmp_path)
    shutil.rmtree(source / "vector.lance")

    vector = VectorEngine(str(source / "vector.lance"), max_workers=1)
    await vector.initialize()
    await vector.upsert("active", "agent-a", [1.0, 0.0, 0.0, 0.0])
    await vector.close()

    backup = tmp_path / "real-backup"
    restore = tmp_path / "real-restore"
    create_backup(source, backup, tmp_path, stores_stopped=True)
    restore_backup(backup, restore, tmp_path)

    restored_vector = VectorEngine(str(restore / "vector.lance"), max_workers=1)
    await restored_vector.initialize()
    assert await restored_vector.get_active_node_ids("agent-a") == {"active"}
    await restored_vector.close()


def test_backup_fails_closed_when_active_writer_holds_lock(tmp_path: Path) -> None:
    from mesa_storage.writer_lock import StorageWriterLock

    source = _synthetic_storage(tmp_path)
    backup = tmp_path / "unsafe-backup"

    # Active writer holds the lock
    with StorageWriterLock.acquire(source, owner="active-runtime"):
        # Caller boolean stores_stopped=True must NOT bypass actual writer lock
        with pytest.raises(RecoveryError, match="active writer"):
            create_backup(source, backup, tmp_path, stores_stopped=True)

    assert not backup.exists()


def test_backup_accepts_valid_held_writer_lock_and_rejects_invalid(
    tmp_path: Path,
) -> None:
    from mesa_storage.writer_lock import StorageWriterLock

    source = _synthetic_storage(tmp_path)
    other_source = tmp_path / "other"
    other_source.mkdir()

    # Pass valid held writer lock
    with StorageWriterLock.acquire(source, owner="rebuild-runner") as writer_lock:
        backup = tmp_path / "valid-backup"
        res = create_backup(
            source, backup, tmp_path, stores_stopped=True, writer_lock=writer_lock
        )
        assert res["valid"] is True

    # Pass released writer lock
    with pytest.raises(RecoveryError, match="storage writer lock is not held"):
        create_backup(
            source,
            tmp_path / "b2",
            tmp_path,
            stores_stopped=True,
            writer_lock=writer_lock,
        )

    # Pass writer lock for different root
    with StorageWriterLock.acquire(other_source, owner="other-runner") as other_lock:
        with pytest.raises(RecoveryError, match="storage writer lock is not held"):
            create_backup(
                source,
                tmp_path / "b3",
                tmp_path,
                stores_stopped=True,
                writer_lock=other_lock,
            )


def test_backup_rejects_forged_writer_lock_capability(tmp_path: Path) -> None:
    """An unrelated open handle cannot be presented as storage quiescence proof."""
    from mesa_storage.writer_lock import StorageWriterLock

    source = _synthetic_storage(tmp_path)
    fake_handle = (tmp_path / "not-the-storage-lock").open("w+", encoding="utf-8")
    forged = StorageWriterLock(fake_handle, source.resolve())
    try:
        with pytest.raises(RecoveryError, match="writer lock is not held"):
            create_backup(
                source,
                tmp_path / "forged-lock-backup",
                tmp_path,
                stores_stopped=True,
                writer_lock=forged,
            )
    finally:
        forged.release()

    assert not (tmp_path / "forged-lock-backup").exists()


def test_backup_manifest_declares_canonical_and_derived_semantics(
    tmp_path: Path,
) -> None:
    source = _synthetic_storage(tmp_path)
    backup = tmp_path / "semantic-backup"

    create_backup(source, backup, tmp_path, stores_stopped=True)

    manifest = json.loads(
        (backup / "mesa-backup-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["consistency_boundary"] == (
        "sqlite-snapshot-plus-exclusive-derived-writer-lock"
    )
    assert manifest["canonical_truth"] == "sqlite"
    assert manifest["sqlite_snapshot_atomicity"] == "per-database"
    assert manifest["derived_projections"] == {
        "graph": "rebuildable",
        "vector": "rebuildable",
        "copy_boundary": "exclusive-storage-writer-lock",
    }
    assert (
        manifest["stores_stopped_input"] == "compatibility-precondition-not-authority"
    )


def test_interrupted_backup_does_not_publish_complete_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mesa_storage.recovery as recovery_mod

    source = _synthetic_storage(tmp_path)
    backup = tmp_path / "interrupted-backup"

    def _fail_manifest(*args: object, **kwargs: object) -> dict:
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(recovery_mod, "_write_manifest", _fail_manifest)

    with pytest.raises(RuntimeError, match="simulated write failure"):
        create_backup(source, backup, tmp_path, stores_stopped=True)

    assert not backup.exists()
    # Ensure incomplete staging dirs were cleaned up
    leftover = [
        p
        for p in tmp_path.iterdir()
        if p.name.startswith(".interrupted-backup.incomplete-")
    ]
    assert len(leftover) == 0


def test_restore_readback_canonical_verification(tmp_path: Path) -> None:
    source = _synthetic_storage(tmp_path)
    db = source / "mesa.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO documents VALUES ('r8-test-doc', 'agent-r8', 'ACTIVE');"
    )
    conn.commit()
    conn.close()

    backup = tmp_path / "r8-backup"
    restore = tmp_path / "r8-restore"

    create_backup(source, backup, tmp_path, stores_stopped=True)
    restore_backup(backup, restore, tmp_path)

    restored_db = restore / "mesa.db"
    conn2 = sqlite3.connect(restored_db)
    row = conn2.execute(
        "SELECT document_id, tenant_id FROM documents WHERE document_id = 'r8-test-doc'"
    ).fetchone()
    assert row == ("r8-test-doc", "agent-r8")
    conn2.close()


def test_backup_sqlite_snapshot_preserves_transaction_atomicity(tmp_path: Path) -> None:
    """SQLite backup snapshots never publish a mixed multi-row transaction."""
    source = tmp_path / "source"
    source.mkdir()
    database = source / "mesa.db"
    with sqlite3.connect(database) as conn:
        conn.executescript("""
            CREATE TABLE alembic_version (version_num TEXT NOT NULL);
            INSERT INTO alembic_version VALUES ('b2e3f4a5c6d7');
            CREATE TABLE related_records (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO related_records VALUES (1, 'before'), (2, 'before');
        """)

    writer = sqlite3.connect(database)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("UPDATE related_records SET value = 'after' WHERE id = 1")
    writer.execute("UPDATE related_records SET value = 'after' WHERE id = 2")
    try:
        backup = tmp_path / "transaction-backup"
        create_backup(source, backup, tmp_path, stores_stopped=True)
    finally:
        writer.rollback()
        writer.close()

    with sqlite3.connect(backup / "mesa.db") as restored:
        values = [
            row[0]
            for row in restored.execute(
                "SELECT value FROM related_records ORDER BY id"
            ).fetchall()
        ]
    assert values in (["before", "before"], ["after", "after"])


def test_backup_sqlite_primitive_includes_committed_wal_state(tmp_path: Path) -> None:
    """Copying only the main file would lose this committed WAL-only record."""
    source = tmp_path / "source"
    source.mkdir()
    database = source / "mesa.db"
    writer = sqlite3.connect(database)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.executescript("""
            CREATE TABLE alembic_version (version_num TEXT NOT NULL);
            INSERT INTO alembic_version VALUES ('b2e3f4a5c6d7');
            CREATE TABLE wal_records (id TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        writer.execute("INSERT INTO wal_records VALUES ('wal-only', 'committed')")
        writer.commit()

        assert (source / "mesa.db-wal").stat().st_size > 0
        with sqlite3.connect(
            f"file:{database}?immutable=1", uri=True
        ) as main_file_only:
            assert (
                main_file_only.execute("SELECT COUNT(*) FROM wal_records").fetchone()[0]
                == 0
            )

        backup = tmp_path / "wal-backup"
        create_backup(source, backup, tmp_path, stores_stopped=True)
    finally:
        writer.close()

    with sqlite3.connect(backup / "mesa.db") as restored:
        assert restored.execute("SELECT id, value FROM wal_records").fetchone() == (
            "wal-only",
            "committed",
        )
