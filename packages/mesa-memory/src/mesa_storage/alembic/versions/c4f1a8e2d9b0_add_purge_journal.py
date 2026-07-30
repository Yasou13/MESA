"""add durable purge journal and node tombstone ownership.

Revision ID: c4f1a8e2d9b0
Revises: 076eef5d1b6c
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c4f1a8e2d9b0"
down_revision: Union[str, Sequence[str], None] = "076eef5d1b6c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create idempotent canonical purge ledger objects without deleting data."""
    bind = op.get_bind()
    columns = {column["name"] for column in bind.dialect.get_columns(bind, "nodes")}
    if "purge_id" not in columns:
        op.execute("ALTER TABLE nodes ADD COLUMN purge_id TEXT")

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_nodes_purge_id ON nodes(purge_id) "
        "WHERE purge_id IS NOT NULL"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS purge_journal (
            purge_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            principal_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            scope TEXT NOT NULL CHECK (scope IN ('agent', 'session')),
            session_id TEXT,
            target_node_ids TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN (
                'PREPARED', 'TOMBSTONED', 'KUZU_APPLIED', 'VECTOR_APPLIED',
                'VERIFIED', 'FINALIZED', 'RETRY_PENDING',
                'COMPENSATION_REQUIRED', 'BLOCKED', 'FAILED_SAFE'
            )),
            kuzu_result TEXT NOT NULL DEFAULT 'PENDING',
            vector_result TEXT NOT NULL DEFAULT 'PENDING',
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK ((scope = 'agent' AND session_id IS NULL) OR
                   (scope = 'session' AND session_id IS NOT NULL))
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_purge_journal_recovery "
        "ON purge_journal(state, updated_at)"
    )


def downgrade() -> None:
    """Drop only the additive journal objects; retain non-destructive node column."""
    op.execute("DROP INDEX IF EXISTS idx_purge_journal_recovery")
    op.execute("DROP TABLE IF EXISTS purge_journal")
    op.execute("DROP INDEX IF EXISTS idx_nodes_purge_id")
