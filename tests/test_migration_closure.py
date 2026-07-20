from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

HEAD = "b2e3f4a5c6d7"
PRE_REMEDIATION_REVISION = "c4f1a8e2d9b0"


def _config(database: Path) -> Config:
    config = Config(str(Path(__file__).parents[1] / "mesa_storage" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database}")
    return config


def _tables(database: Path) -> set[str]:
    connection = sqlite3.connect(database)
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    connection.close()
    return {str(row[0]) for row in rows}


def test_fresh_upgrade_has_one_head_and_complete_durable_schema(tmp_path: Path) -> None:
    database = tmp_path / "fresh.db"
    config = _config(database)
    command.upgrade(config, "head")
    command.upgrade(config, "head")

    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD
    connection.close()
    assert {
        "purge_journal",
        "dispatch_journal",
        "dispatch_queue",
        "dispatch_completion_receipts",
        "session_finalization_journal",
        "lancedb_wal",
    } <= _tables(database)


def test_pre_remediation_upgrade_preserves_existing_data_and_backfills_state(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    config = _config(database)
    command.upgrade(config, PRE_REMEDIATION_REVISION)
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO nodes (id, entity_name, type, content_payload, created_at, agent_id, session_id) "
        "VALUES ('legacy-node', 'Legacy', 'ENTITY', '{}', '2026-01-01', 'agent-a', 'session-a')"
    )
    connection.execute(
        "INSERT INTO lancedb_wal (id, agent_id, vector, metadata) VALUES ('legacy-wal', 'agent-a', X'01', '{}')"
    )
    connection.commit()
    connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(database)
    assert connection.execute("SELECT entity_name FROM nodes WHERE id='legacy-node'").fetchone()[0] == "Legacy"
    row = connection.execute(
        "SELECT mutation_id, idempotency_key, state, vector_state, graph_state, retry_limit "
        "FROM lancedb_wal WHERE id='legacy-wal'"
    ).fetchone()
    assert row == ("legacy-wal", "wal:legacy-wal", "PENDING", "PENDING", "NOT_REQUIRED", 3)
    assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.close()
