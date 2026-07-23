from __future__ import annotations

import sqlite3
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from mesa_storage import schema_contract
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine

HEAD = "9a1b2c3d4e5f"
PRE_REMEDIATION_REVISION = "c4f1a8e2d9b0"


def _config(database: Path) -> Config:
    config = Config(str(Path(__file__).parents[1] / "mesa_storage" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database}")
    return config


def _tables(database: Path) -> set[str]:
    connection = sqlite3.connect(database)
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    connection.close()
    return {str(row[0]) for row in rows}


def _legacy_schema(database: Path, family: str) -> None:
    """Create released pre-Alembic layouts with one preserved data row."""
    connection = sqlite3.connect(database)
    connection.executescript("""
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY,
            entity_name TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'ENTITY',
            is_consolidated INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            invalid_at TEXT DEFAULT NULL,
            deleted_at TEXT DEFAULT NULL,
            agent_id TEXT NOT NULL DEFAULT '__unset__',
            session_id TEXT NOT NULL DEFAULT '__unset__'
        );
        CREATE INDEX idx_nodes_active ON nodes(invalid_at) WHERE invalid_at IS NULL;
        CREATE INDEX idx_nodes_entity_name ON nodes(entity_name COLLATE NOCASE) WHERE invalid_at IS NULL;
        CREATE INDEX idx_nodes_agent ON nodes(agent_id, session_id) WHERE invalid_at IS NULL;
        CREATE INDEX idx_nodes_unconsolidated ON nodes(is_consolidated) WHERE is_consolidated = 0 AND invalid_at IS NULL;
        CREATE INDEX idx_nodes_soft_deleted ON nodes(deleted_at) WHERE deleted_at IS NOT NULL;
        CREATE VIRTUAL TABLE nodes_fts USING fts5(entity_name, type, content='nodes', content_rowid='rowid');
        CREATE TRIGGER trg_nodes_fts_insert AFTER INSERT ON nodes BEGIN
            INSERT INTO nodes_fts(rowid, entity_name, type) VALUES (NEW.rowid, NEW.entity_name, NEW.type);
        END;
        CREATE TRIGGER trg_nodes_fts_delete AFTER DELETE ON nodes BEGIN
            INSERT INTO nodes_fts(nodes_fts, rowid, entity_name, type) VALUES ('delete', OLD.rowid, OLD.entity_name, OLD.type);
        END;
        CREATE TRIGGER trg_nodes_fts_update AFTER UPDATE ON nodes BEGIN
            INSERT INTO nodes_fts(nodes_fts, rowid, entity_name, type) VALUES ('delete', OLD.rowid, OLD.entity_name, OLD.type);
            INSERT INTO nodes_fts(rowid, entity_name, type) VALUES (NEW.rowid, NEW.entity_name, NEW.type);
        END;
        """)
    if family in {"v0.3", "v0.4"}:
        connection.executescript("""
            CREATE TABLE edges (
                id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
                relation_type TEXT NOT NULL, weight REAL NOT NULL DEFAULT 1.0,
                created_at TEXT NOT NULL, invalid_at TEXT DEFAULT NULL,
                agent_id TEXT NOT NULL DEFAULT '__unset__'
            );
            """)
        connection.execute(
            "INSERT INTO edges VALUES ('edge-1', 'legacy-node', 'legacy-node', 'SELF', 1.0, '2026-01-01', NULL, 'agent-a')"
        )
    if family in {"v0.4", "v0.5"}:
        connection.executescript("""
            CREATE TABLE routing_telemetry (
                id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, record_id TEXT NOT NULL,
                small_model_decision INTEGER NOT NULL, small_model_confidence REAL NOT NULL,
                dual_llm_decision INTEGER NOT NULL, is_hallucination INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE raw_logs (
                id INTEGER PRIMARY KEY, agent_id TEXT NOT NULL, payload JSON NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX idx_raw_logs_session
                ON raw_logs(json_extract(payload, '$.agent_id'), json_extract(payload, '$.session_id'));
            """)
    connection.execute(
        "INSERT INTO nodes VALUES ('legacy-node', 'Legacy', 'ENTITY', 0, '2026-01-01', NULL, NULL, 'agent-a', 'session-a')"
    )
    connection.commit()
    connection.close()


def _adoption_config(database: Path) -> Config:
    config = _config(database)
    config.cmd_opts = Namespace(x=["mesa_legacy=adopt"])
    return config


def test_fresh_upgrade_has_one_head_and_complete_durable_schema(tmp_path: Path) -> None:
    database = tmp_path / "fresh.db"
    config = _config(database)
    command.upgrade(config, "head")
    command.upgrade(config, "head")

    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert (
        connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        == HEAD
    )
    connection.close()
    assert {
        "purge_journal",
        "dispatch_journal",
        "dispatch_queue",
        "dispatch_completion_receipts",
        "session_finalization_journal",
        "lancedb_wal",
    } <= _tables(database)


def test_pre_remediation_upgrade_preserves_existing_data_and_backfills_state(
    tmp_path: Path,
) -> None:
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
    assert (
        connection.execute(
            "SELECT entity_name FROM nodes WHERE id='legacy-node'"
        ).fetchone()[0]
        == "Legacy"
    )
    row = connection.execute(
        "SELECT mutation_id, idempotency_key, state, vector_state, graph_state, retry_limit "
        "FROM lancedb_wal WHERE id='legacy-wal'"
    ).fetchone()
    assert row == (
        "legacy-wal",
        "wal:legacy-wal",
        "PENDING",
        "PENDING",
        "NOT_REQUIRED",
        3,
    )
    assert (
        connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        == HEAD
    )
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.close()


@pytest.mark.parametrize("family", ["v0.3", "v0.4", "v0.5"])
def test_known_legacy_schema_requires_explicit_adoption_and_preserves_data(
    tmp_path: Path, family: str
) -> None:
    database = tmp_path / f"{family}.db"
    _legacy_schema(database, family)

    with pytest.raises(schema_contract.LegacySchemaAdoptionRequired):
        command.upgrade(_config(database), "head")

    connection = sqlite3.connect(database)
    assert "alembic_version" not in _tables(database)
    assert connection.execute("SELECT entity_name FROM nodes").fetchone()[0] == "Legacy"
    connection.close()

    command.upgrade(_adoption_config(database), "head")
    command.upgrade(_adoption_config(database), "head")

    connection = sqlite3.connect(database)
    assert connection.execute("SELECT content_payload FROM nodes").fetchone()[0] == ""
    assert (
        connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        == HEAD
    )
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    if family in {"v0.3", "v0.4"}:
        assert connection.execute("SELECT count(*) FROM edges").fetchone()[0] == 1
    connection.close()


def test_unknown_unmanaged_schema_is_rejected_without_a_revision(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unknown.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE nodes (id TEXT PRIMARY KEY, entity_name TEXT)")
    connection.execute("INSERT INTO nodes VALUES ('unknown-node', 'Unknown')")
    connection.commit()
    connection.close()

    with pytest.raises(schema_contract.SchemaContractError, match="unmanaged database"):
        command.upgrade(_config(database), "head")

    connection = sqlite3.connect(database)
    assert "alembic_version" not in _tables(database)
    assert (
        connection.execute("SELECT entity_name FROM nodes").fetchone()[0] == "Unknown"
    )
    connection.close()


@pytest.mark.asyncio
async def test_startup_path_refuses_unmanaged_legacy_schema(tmp_path: Path) -> None:
    database = tmp_path / "startup-legacy.db"
    _legacy_schema(database, "v0.5")
    engine = AsyncEngine(str(database))
    with pytest.raises(schema_contract.LegacySchemaAdoptionRequired):
        await initialize_schema(engine)

    connection = sqlite3.connect(database)
    assert "alembic_version" not in _tables(database)
    assert connection.execute("SELECT entity_name FROM nodes").fetchone()[0] == "Legacy"
    connection.close()


def test_claimed_managed_revision_with_missing_base_column_is_rejected(
    tmp_path: Path,
) -> None:
    database = tmp_path / "claimed-managed.db"
    _legacy_schema(database, "v0.5")
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE system_config (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE lancedb_wal (id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, vector BLOB NOT NULL, metadata JSON)"
    )
    connection.execute(
        "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
    )
    connection.execute("INSERT INTO alembic_version VALUES (?)", (HEAD,))
    connection.commit()
    connection.close()

    with pytest.raises(schema_contract.SchemaContractError, match="nodes columns"):
        command.upgrade(_config(database), "head")

    connection = sqlite3.connect(database)
    assert (
        connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        == HEAD
    )
    assert "content_payload" not in {
        row[1] for row in connection.execute("PRAGMA table_info(nodes)")
    }
    connection.close()


def test_schema_contract_rejects_wrong_node_primary_key_and_default(
    tmp_path: Path,
) -> None:
    database = tmp_path / "invalid-node-contract.db"
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    with engine.connect() as connection:
        connection.exec_driver_sql("""
        CREATE TABLE nodes (
            id TEXT NOT NULL,
            entity_name TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'ENTITY',
            content_payload TEXT NOT NULL DEFAULT 'unexpected',
            is_consolidated INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            invalid_at TEXT DEFAULT NULL,
            deleted_at TEXT DEFAULT NULL,
            agent_id TEXT NOT NULL DEFAULT '__unset__',
            session_id TEXT NOT NULL DEFAULT '__unset__'
        );
        """)
        snapshot = schema_contract.inspect_schema(connection)
        with pytest.raises(schema_contract.SchemaContractError, match="primary-key"):
            schema_contract._assert_base_node_contract(snapshot)
        connection.exec_driver_sql("DROP TABLE nodes")
        connection.exec_driver_sql("""
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY,
            entity_name TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'ENTITY',
            content_payload TEXT NOT NULL DEFAULT 'unexpected',
            is_consolidated INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            invalid_at TEXT DEFAULT NULL,
            deleted_at TEXT DEFAULT NULL,
            agent_id TEXT NOT NULL DEFAULT '__unset__',
            session_id TEXT NOT NULL DEFAULT '__unset__'
        );
        """)
        snapshot = schema_contract.inspect_schema(connection)
        with pytest.raises(
            schema_contract.SchemaContractError, match="content_payload.*default"
        ):
            schema_contract._assert_base_node_contract(snapshot)
    engine.dispose()


def test_adoption_rolls_back_if_base_stamp_fails(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "rollback.db"
    _legacy_schema(database, "v0.5")

    def fail_stamp(*_args, **_kwargs) -> None:
        raise RuntimeError("simulated stamp failure")

    monkeypatch.setattr(schema_contract, "_stamp_base_revision", fail_stamp)
    with pytest.raises(RuntimeError, match="simulated stamp failure"):
        command.upgrade(_adoption_config(database), "head")

    connection = sqlite3.connect(database)
    assert "alembic_version" not in _tables(database)
    assert "content_payload" not in {
        row[1] for row in connection.execute("PRAGMA table_info(nodes)")
    }
    assert connection.execute("SELECT entity_name FROM nodes").fetchone()[0] == "Legacy"
    connection.close()


def test_offline_operator_cli_adopts_recognised_legacy_schema(tmp_path: Path) -> None:
    database = tmp_path / "operator-cli.db"
    _legacy_schema(database, "v0.5")
    ini_path = Path(__file__).parents[1] / "mesa_storage" / "alembic.ini"
    operator_ini = tmp_path / "operator-alembic.ini"
    operator_ini.write_text(
        ini_path.read_text(encoding="utf-8")
        .replace(
            "script_location = %(here)s/alembic",
            f"script_location = {ini_path.parent / 'alembic'}",
        )
        .replace(
            "sqlalchemy.url = driver://user:pass@localhost/dbname",
            f"sqlalchemy.url = sqlite+aiosqlite:///{database}",
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(operator_ini),
            "-x",
            "mesa_legacy=adopt",
            "upgrade",
            "head",
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr

    connection = sqlite3.connect(database)
    assert (
        connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        == HEAD
    )
    assert connection.execute("SELECT content_payload FROM nodes").fetchone()[0] == ""
    connection.close()
