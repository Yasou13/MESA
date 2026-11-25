from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.engine import Engine

from mesa_storage import schema_contract
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine

# Explicitly anchor the expected HEAD migration to prevent unreviewed schema drift
# Update this ONLY when a new migration has been peer-reviewed.
HEAD = "c2f3a4b5c6d7"
PREVIOUS_093_HEAD = "e6f7a8b9c0d1"
PREVIOUS_HEAD = "fd4e5f6a7b8c"
ROUND7_PREDECESSOR = "0a7b8c9d0e1f"


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


def test_fresh_upgrade_has_one_head_and_complete_durable_schema(tmp_path: Path) -> None:
    database = tmp_path / "fresh.db"
    config = _config(database)
    server_logger = logging.getLogger("uvicorn.error")
    storage_logger = logging.getLogger("MESA_Storage")
    server_logger.disabled = storage_logger.disabled = False
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    assert not server_logger.disabled
    assert not storage_logger.disabled

    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert (
        connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        == HEAD
    )
    mutation_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(memory_mutations)").fetchall()
    }
    assert "embedding_provider" in mutation_columns
    connection.close()
    tables = _tables(database)
    assert {
        "dispatch_journal",
        "dispatch_queue",
        "dispatch_completion_receipts",
        "session_finalization_journal",
        "system_operations",
        "system_operation_events",
        "projection_generations",
        "projection_runtime",
        "v4_assertions",
        "v4_assertions_fts",
        "documents",
        "document_revisions",
        "source_chunks",
    } <= tables
    assert not (
        {
            "nodes",
            "nodes_fts",
            "lancedb_wal",
            "purge_journal",
            "system_config",
            "routing_telemetry",
        }
        & tables
    )


def test_postflight_requires_active_document_head_index(tmp_path: Path) -> None:
    database = tmp_path / "missing-active-head-index.db"
    config = _config(database)
    command.upgrade(config, "head")
    connection = sqlite3.connect(database)
    connection.execute("DROP INDEX uq_active_document_revision")
    with pytest.raises(
        schema_contract.SchemaContractError, match="ACTIVE document-head"
    ):
        schema_contract.validate_postflight(connection, config, require_head=True)
    connection.close()


def test_previous_head_upgrades_with_legacy_projection_generation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "previous-head.db"
    config = _config(database)
    command.upgrade(config, PREVIOUS_HEAD)
    assert "system_operations" in _tables(database)
    connection = sqlite3.connect(database)
    assert (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' "
            "AND name = 'uq_active_document_revision'"
        ).fetchone()
        is None
    )
    connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(database)
    generation = connection.execute(
        "SELECT generation_id, generation_kind, lifecycle_state, "
        "vector_relative_path, graph_relative_path "
        "FROM projection_generations"
    ).fetchone()
    assert generation == (
        "legacy",
        "LEGACY",
        "ACTIVE",
        "vector.lance",
        "graph_unused",
    )
    runtime = connection.execute(
        "SELECT runtime_id, active_generation_id, previous_generation_id, "
        "fencing_token FROM projection_runtime"
    ).fetchone()
    assert runtime == (1, "legacy", None, 0)
    active_index = connection.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type='index' AND name='uq_system_operations_active_rebuild'"
    ).fetchone()[0]
    assert "RETRYABLE_FAILED" in active_index
    assert {
        "trg_system_operation_events_no_update",
        "trg_system_operation_events_no_delete",
    } <= {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )
    }
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert (
        connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        == HEAD
    )
    connection.close()


def test_active_head_forward_migration_repairs_previous_release_duplicates(
    tmp_path: Path,
) -> None:
    database = tmp_path / "duplicate-active-heads.db"
    config = _config(database)
    command.upgrade(config, PREVIOUS_HEAD)
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO tenants (tenant_id, display_name) VALUES ('tenant', 'Tenant')"
    )
    connection.execute(
        "INSERT INTO workspaces (workspace_id, tenant_id, name) "
        "VALUES ('workspace', 'tenant', 'Workspace')"
    )
    connection.execute(
        "INSERT INTO datasets (dataset_id, tenant_id, workspace_id, name) "
        "VALUES ('dataset', 'tenant', 'workspace', 'Dataset')"
    )
    connection.execute(
        "INSERT INTO documents (document_id, tenant_id, dataset_id, title) "
        "VALUES ('document', 'tenant', 'dataset', 'Document')"
    )
    connection.execute(
        "INSERT INTO document_revisions (revision_id, tenant_id, document_id, "
        "revision_number, content_hash, manifest_hash, status, created_at) VALUES "
        "('r1', 'tenant', 'document', 1, ?, ?, 'ACTIVE', '2026-01-01'), "
        "('r2', 'tenant', 'document', 2, ?, ?, 'ACTIVE', '2026-01-02'), "
        "('r3', 'tenant', 'document', 3, ?, ?, 'PENDING', '2026-01-03')",
        ("1" * 64, "a" * 64, "2" * 64, "b" * 64, "3" * 64, "c" * 64),
    )
    connection.commit()
    connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT revision_id, status FROM document_revisions " "ORDER BY revision_number"
    ).fetchall() == [
        ("r1", "SUPERSEDED"),
        ("r2", "ACTIVE"),
        ("r3", "PENDING"),
    ]
    frozen = dict(
        connection.execute(
            "SELECT revision_id, manifest_frozen_at FROM document_revisions"
        ).fetchall()
    )
    assert frozen["r1"] is not None
    assert frozen["r2"] is not None
    assert frozen["r3"] is None
    # Pre-Round-7 content hashes have mixed provenance.  Migration must not
    # relabel them as caller-declared whole-revision hashes.
    assert connection.execute(
        "SELECT revision_id, declared_content_hash FROM document_revisions "
        "ORDER BY revision_number"
    ).fetchall() == [("r1", None), ("r2", None), ("r3", None)]
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' "
        "AND name = 'uq_active_document_revision'"
    ).fetchone() == (1,)
    connection.close()


def test_round7_predecessor_preserves_hash_manifest_chunk_and_catalog_data(
    tmp_path: Path,
) -> None:
    database = tmp_path / "round7-predecessor.db"
    config = _config(database)
    command.upgrade(config, ROUND7_PREDECESSOR)
    connection = sqlite3.connect(database)
    connection.executescript("""
        INSERT INTO tenants (tenant_id, display_name)
        VALUES ('tenant-r7', 'Tenant R7');
        INSERT INTO v4_catalog_identities
            (tenant_id, kind, external_id, physical_id)
        VALUES
            ('tenant-r7', 'workspace', 'workspace-public', 'workspace-physical'),
            ('tenant-r7', 'dataset', 'dataset-public', 'dataset-physical'),
            ('tenant-r7', 'document', 'document-public', 'document-physical'),
            ('tenant-r7', 'revision', 'revision-public', 'revision-physical'),
            ('tenant-r7', 'chunk', 'chunk-public', 'chunk-physical');
        INSERT INTO workspaces (workspace_id, tenant_id, name)
        VALUES ('workspace-physical', 'tenant-r7', 'Workspace');
        INSERT INTO datasets (dataset_id, tenant_id, workspace_id, name)
        VALUES ('dataset-physical', 'tenant-r7', 'workspace-physical', 'Dataset');
        INSERT INTO documents (document_id, tenant_id, dataset_id, title)
        VALUES ('document-physical', 'tenant-r7', 'dataset-physical', 'Document');
        INSERT INTO document_revisions
            (revision_id, tenant_id, document_id, revision_number, content_hash,
             manifest_hash, manifest_frozen_at, status)
        VALUES
            ('revision-physical', 'tenant-r7', 'document-physical', 1,
             'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
             'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
             CURRENT_TIMESTAMP, 'ACTIVE');
        INSERT INTO source_chunks
            (chunk_id, tenant_id, dataset_id, revision_id, ordinal,
             content_payload, content_hash, source_ref)
        VALUES
            ('chunk-physical', 'tenant-r7', 'dataset-physical',
             'revision-physical', 0, 'payload',
             'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
             'source');
        """)
    connection.commit()
    connection.close()

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT content_hash, declared_content_hash, manifest_hash, status "
        "FROM document_revisions WHERE revision_id = 'revision-physical'"
    ).fetchone() == (
        "a" * 64,
        None,
        "b" * 64,
        "ACTIVE",
    )
    assert connection.execute(
        "SELECT content_payload, content_hash, source_ref FROM source_chunks "
        "WHERE chunk_id = 'chunk-physical'"
    ).fetchone() == ("payload", "c" * 64, "source")
    assert connection.execute(
        "SELECT external_id, physical_id FROM v4_catalog_identities "
        "WHERE tenant_id = 'tenant-r7' AND kind = 'dataset'"
    ).fetchone() == ("dataset-public", "dataset-physical")
    content_hash_column = next(
        row
        for row in connection.execute(
            "PRAGMA table_info(document_revisions)"
        ).fetchall()
        if row[1] == "content_hash"
    )
    assert content_hash_column[3] == 0
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.close()


def test_round7_nullable_hash_migration_failure_is_reentrant(
    tmp_path: Path,
) -> None:
    database = tmp_path / "round7-failure.db"
    config = _config(database)
    command.upgrade(config, "a2b3c4d5e6f7")
    connection = sqlite3.connect(database)
    connection.executescript("""
        INSERT INTO tenants (tenant_id, display_name) VALUES ('tenant', 'Tenant');
        INSERT INTO workspaces (workspace_id, tenant_id, name)
        VALUES ('workspace', 'tenant', 'Workspace');
        INSERT INTO datasets (dataset_id, tenant_id, workspace_id, name)
        VALUES ('dataset', 'tenant', 'workspace', 'Dataset');
        INSERT INTO documents (document_id, tenant_id, dataset_id, title)
        VALUES ('document', 'tenant', 'dataset', 'Document');
        INSERT INTO document_revisions
            (revision_id, tenant_id, document_id, revision_number, content_hash,
             status, declared_content_hash)
        VALUES ('revision', 'tenant', 'document', 1,
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                'PENDING', NULL);
        """)
    connection.commit()
    connection.close()

    def fail_batch_copy(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if "INSERT INTO _alembic_tmp_document_revisions" in statement:
            raise RuntimeError("injected Round 7 migration failure")

    event.listen(Engine, "before_cursor_execute", fail_batch_copy)
    try:
        with pytest.raises(RuntimeError, match="injected Round 7 migration failure"):
            command.upgrade(config, "head")
    finally:
        event.remove(Engine, "before_cursor_execute", fail_batch_copy)

    connection = sqlite3.connect(database)
    assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
        "a2b3c4d5e6f7",
    )
    assert connection.execute(
        "SELECT content_hash FROM document_revisions WHERE revision_id = 'revision'"
    ).fetchone() == ("a" * 64,)
    assert (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = '_alembic_tmp_document_revisions'"
        ).fetchone()
        is None
    )
    connection.close()

    command.upgrade(config, "head")
    command.upgrade(config, "head")
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
        HEAD,
    )
    assert connection.execute(
        "SELECT content_hash FROM document_revisions WHERE revision_id = 'revision'"
    ).fetchone() == ("a" * 64,)
    connection.close()


def test_operation_schema_enforces_single_active_rebuild_and_append_only_events(
    tmp_path: Path,
) -> None:
    database = tmp_path / "operation-constraints.db"
    command.upgrade(_config(database), "head")
    connection = sqlite3.connect(database)
    operation_values = (
        "PROJECTION_REBUILD",
        "STORAGE_ROOT",
        "default",
        "admin-a",
        "a" * 64,
        "PENDING",
    )
    connection.execute(
        "INSERT INTO system_operations ("
        "operation_id, operation_kind, scope_kind, scope_key, "
        "requested_by_principal_id, idempotency_key, payload_hash, state"
        ") VALUES ('operation-a', ?, ?, ?, ?, 'key-a', ?, ?)",
        operation_values,
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO system_operations ("
            "operation_id, operation_kind, scope_kind, scope_key, "
            "requested_by_principal_id, idempotency_key, payload_hash, state"
            ") VALUES ('operation-b', ?, ?, ?, ?, 'key-b', ?, ?)",
            operation_values,
        )

    connection.execute(
        "INSERT INTO system_operation_events ("
        "event_id, operation_id, sequence_number, event_type, to_state, "
        "fencing_token, attempt_count"
        ") VALUES ('event-a', 'operation-a', 1, 'SUBMITTED', 'PENDING', 0, 0)"
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(
            "UPDATE system_operation_events SET event_type='CHANGED' "
            "WHERE event_id='event-a'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(
            "DELETE FROM system_operation_events WHERE event_id='event-a'"
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO projection_generations ("
            "generation_id, generation_kind, lifecycle_state, "
            "vector_relative_path, graph_relative_path"
            ") VALUES ('escaped', 'REBUILD', 'STAGING', "
            "'/absolute/vector.lance', 'generations/escaped/graph_unused')"
        )
    connection.close()


def test_destructive_alembic_downgrade_is_refused_without_schema_mutation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "forward-only.db"
    config = _config(database)
    command.upgrade(config, "head")

    with pytest.raises(RuntimeError, match="forward-only"):
        command.downgrade(config, "base")

    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert (
        connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        == HEAD
    )
    connection.close()


def test_093_store_upgrades_to_head_and_drops_v3_tables_preserving_v4_data(
    tmp_path: Path,
) -> None:
    database = tmp_path / "v093-store.db"
    config = _config(database)
    command.upgrade(config, PREVIOUS_093_HEAD)
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO tenants (tenant_id, display_name) VALUES ('tenant-093', 'Tenant 093')"
    )
    connection.execute(
        "INSERT INTO raw_logs (agent_id, payload, status) VALUES ('agent-a', '{}', 'processed')"
    )
    connection.commit()
    connection.close()

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    tables = _tables(database)
    assert not (
        {
            "nodes",
            "nodes_fts",
            "lancedb_wal",
            "purge_journal",
            "system_config",
            "routing_telemetry",
        }
        & tables
    )
    connection = sqlite3.connect(database)
    assert (
        connection.execute(
            "SELECT display_name FROM tenants WHERE tenant_id = 'tenant-093'"
        ).fetchone()[0]
        == "Tenant 093"
    )
    assert (
        connection.execute("SELECT COUNT(*) FROM raw_logs").fetchone()[0] == 1
    )
    assert (
        connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        == HEAD
    )
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    connection.close()


def test_unknown_unmanaged_schema_is_rejected_without_a_revision(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unknown.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE unmanaged_items (id TEXT PRIMARY KEY, name TEXT)")
    connection.execute("INSERT INTO unmanaged_items VALUES ('item-1', 'Unknown')")
    connection.commit()
    connection.close()

    with pytest.raises(schema_contract.SchemaContractError, match="unmanaged database"):
        command.upgrade(_config(database), "head")

    connection = sqlite3.connect(database)
    assert "alembic_version" not in _tables(database)
    assert (
        connection.execute("SELECT name FROM unmanaged_items").fetchone()[0] == "Unknown"
    )
    connection.close()


@pytest.mark.asyncio
async def test_startup_path_refuses_unmanaged_schema(tmp_path: Path) -> None:
    database = tmp_path / "startup-unmanaged.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE unmanaged_items (id TEXT PRIMARY KEY, name TEXT)")
    connection.commit()
    connection.close()

    engine = AsyncEngine(str(database))
    with pytest.raises(schema_contract.SchemaContractError, match="unmanaged database"):
        await initialize_schema(engine)

    assert "alembic_version" not in _tables(database)


def test_claimed_head_revision_with_missing_v4_table_is_rejected(
    tmp_path: Path,
) -> None:
    database = tmp_path / "claimed-head.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE raw_logs (id INTEGER PRIMARY KEY, agent_id TEXT NOT NULL, payload JSON NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
    )
    connection.execute("INSERT INTO alembic_version VALUES (?)", (HEAD,))
    connection.commit()
    connection.close()

    with pytest.raises(schema_contract.SchemaContractError, match="V4 head tables"):
        command.upgrade(_config(database), "head")

