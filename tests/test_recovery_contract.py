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
    connection.executescript(
        """
        CREATE TABLE alembic_version (version_num TEXT NOT NULL);
        INSERT INTO alembic_version VALUES ('b2e3f4a5c6d7');
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, invalid_at TEXT,
            deleted_at TEXT, purge_id TEXT
        );
        CREATE TABLE purge_journal (
            purge_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL,
            target_node_ids TEXT NOT NULL, state TEXT NOT NULL
        );
        CREATE TABLE lancedb_wal (state TEXT NOT NULL);
        CREATE TABLE dispatch_queue (state TEXT NOT NULL);
        CREATE TABLE session_finalization_journal (state TEXT NOT NULL);
        INSERT INTO nodes VALUES ('purged', 'agent-a', 'now', 'now', 'purge-1');
        INSERT INTO nodes VALUES ('active', 'agent-a', NULL, NULL, NULL);
        INSERT INTO purge_journal VALUES ('purge-1', 'agent-a', '["purged"]', 'FINALIZED');
        INSERT INTO lancedb_wal VALUES ('BLOCKED');
        INSERT INTO dispatch_queue VALUES ('COMPLETED');
        INSERT INTO session_finalization_journal VALUES ('RETRY_PENDING');
        """
    )
    connection.commit()
    connection.close()
    (source / "vector.lance").mkdir()
    (source / "vector.lance" / "data.bin").write_bytes(b"vector")
    (source / "kuzu_db").mkdir()
    (source / "kuzu_db" / "data.kz").write_bytes(b"graph")
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
    assert created["sqlite"]["mesa.db"]["purge"] == {"records": 1, "verified_targets": 1}
    assert created["sqlite"]["mesa.db"]["wal_states"] == {"BLOCKED": 1}
    assert created["completion_receipt_files"] == ["dlq.jsonl.receipts.jsonl"]

    restored = restore_backup(backup, restore, tmp_path)
    assert restored["restored"] is True
    result = validate_snapshot(restore)
    assert result["sqlite"]["mesa.db"]["dispatch_states"] == {"COMPLETED": 1}
    connection = sqlite3.connect(restore / "mesa.db")
    assert connection.execute("SELECT COUNT(*) FROM nodes WHERE deleted_at IS NULL").fetchone()[0] == 1
    connection.close()
    assert (restore / "vector.lance" / "data.bin").read_bytes() == b"vector"
    assert (restore / "kuzu_db" / "data.kz").read_bytes() == b"graph"


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
    connection.execute("UPDATE nodes SET invalid_at = NULL, deleted_at = NULL WHERE id = 'purged'")
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
async def test_real_lancedb_and_kuzu_reopen_after_restore(tmp_path: Path) -> None:
    from mesa_storage import kuzu_setup
    from mesa_storage.kuzu_provider import KuzuGraphProvider
    from mesa_storage.vector_engine import VectorEngine

    source = _synthetic_storage(tmp_path)
    shutil.rmtree(source / "vector.lance")
    shutil.rmtree(source / "kuzu_db")

    vector = VectorEngine(str(source / "vector.lance"), max_workers=1)
    await vector.initialize()
    await vector.upsert("active", "agent-a", [1.0, 0.0, 0.0, 0.0])
    await vector.close()
    kuzu_setup.initialize_schema(str(source / "kuzu_db"))
    graph = KuzuGraphProvider(str(source / "kuzu_db"), max_workers=1)
    await graph.initialize()
    await graph.insert_node("active", "Active", "agent-a")
    await graph.close()

    backup = tmp_path / "real-backup"
    restore = tmp_path / "real-restore"
    create_backup(source, backup, tmp_path, stores_stopped=True)
    restore_backup(backup, restore, tmp_path)

    restored_vector = VectorEngine(str(restore / "vector.lance"), max_workers=1)
    await restored_vector.initialize()
    assert await restored_vector.get_active_node_ids("agent-a") == {"active"}
    await restored_vector.close()
    restored_graph = KuzuGraphProvider(str(restore / "kuzu_db"), max_workers=1)
    await restored_graph.initialize()
    assert await restored_graph.verify_nodes_absent(agent_id="agent-a", node_ids=["purged"])
    assert not await restored_graph.verify_nodes_absent(agent_id="agent-a", node_ids=["active"])
    await restored_graph.close()
