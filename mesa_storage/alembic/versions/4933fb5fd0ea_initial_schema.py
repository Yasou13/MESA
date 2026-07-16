"""initial_schema

Revision ID: 4933fb5fd0ea
Revises:
Create Date: 2026-07-09 10:23:05.380464

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4933fb5fd0ea"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
    CREATE TABLE IF NOT EXISTS nodes (
        id               TEXT    PRIMARY KEY,
        entity_name      TEXT    NOT NULL,
        type             TEXT    NOT NULL DEFAULT 'ENTITY',
        content_payload  TEXT    NOT NULL DEFAULT '',
        is_consolidated  INTEGER NOT NULL DEFAULT 0,
        created_at       TEXT    NOT NULL,
        invalid_at       TEXT    DEFAULT NULL,
        deleted_at       TEXT    DEFAULT NULL,
        agent_id         TEXT    NOT NULL DEFAULT '__unset__',
        session_id       TEXT    NOT NULL DEFAULT '__unset__'
    );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_nodes_active ON nodes(invalid_at) WHERE invalid_at IS NULL;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_nodes_entity_name ON nodes(entity_name COLLATE NOCASE) WHERE invalid_at IS NULL;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_nodes_agent ON nodes(agent_id, session_id) WHERE invalid_at IS NULL;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_nodes_unconsolidated ON nodes(is_consolidated) WHERE is_consolidated = 0 AND invalid_at IS NULL;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_nodes_soft_deleted ON nodes(deleted_at) WHERE deleted_at IS NOT NULL;"
    )

    op.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts
    USING fts5(
        entity_name,
        type,
        content='nodes',
        content_rowid='rowid'
    );
    """)
    op.execute("""
    CREATE TRIGGER IF NOT EXISTS trg_nodes_fts_insert
    AFTER INSERT ON nodes
    BEGIN
        INSERT INTO nodes_fts(rowid, entity_name, type)
        VALUES (NEW.rowid, NEW.entity_name, NEW.type);
    END;
    """)
    op.execute("""
    CREATE TRIGGER IF NOT EXISTS trg_nodes_fts_delete
    AFTER DELETE ON nodes
    BEGIN
        INSERT INTO nodes_fts(nodes_fts, rowid, entity_name, type)
        VALUES ('delete', OLD.rowid, OLD.entity_name, OLD.type);
    END;
    """)
    op.execute("""
    CREATE TRIGGER IF NOT EXISTS trg_nodes_fts_update
    AFTER UPDATE ON nodes
    BEGIN
        INSERT INTO nodes_fts(nodes_fts, rowid, entity_name, type)
        VALUES ('delete', OLD.rowid, OLD.entity_name, OLD.type);
        INSERT INTO nodes_fts(rowid, entity_name, type)
        VALUES (NEW.rowid, NEW.entity_name, NEW.type);
    END;
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS routing_telemetry (
        id                     TEXT    PRIMARY KEY,
        agent_id               TEXT    NOT NULL,
        record_id              TEXT    NOT NULL,
        small_model_decision   INTEGER NOT NULL,
        small_model_confidence REAL    NOT NULL,
        dual_llm_decision      INTEGER NOT NULL,
        is_hallucination       INTEGER NOT NULL,
        created_at             TEXT    NOT NULL
    );
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS raw_logs (
        id         INTEGER PRIMARY KEY,
        agent_id   TEXT    NOT NULL,
        payload    JSON    NOT NULL,
        status     TEXT    NOT NULL DEFAULT 'DEFERRED',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_raw_logs_session ON raw_logs(json_extract(payload, '$.agent_id'), json_extract(payload, '$.session_id'));"
    )

    op.execute("""
    CREATE TABLE IF NOT EXISTS system_config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """)
    op.execute(
        "INSERT OR IGNORE INTO system_config (key, value) VALUES ('lancedb_is_migrating', 'false');"
    )
    op.execute("""
    CREATE TABLE IF NOT EXISTS lancedb_wal (
        id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL,
        vector BLOB NOT NULL,
        metadata JSON,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE IF EXISTS lancedb_wal;")
    op.execute("DROP TABLE IF EXISTS system_config;")
    op.execute("DROP TABLE IF EXISTS raw_logs;")
    op.execute("DROP TABLE IF EXISTS routing_telemetry;")
    op.execute("DROP TRIGGER IF EXISTS trg_nodes_fts_update;")
    op.execute("DROP TRIGGER IF EXISTS trg_nodes_fts_delete;")
    op.execute("DROP TRIGGER IF EXISTS trg_nodes_fts_insert;")
    op.execute("DROP TABLE IF EXISTS nodes_fts;")
    op.execute("DROP TABLE IF EXISTS nodes;")
