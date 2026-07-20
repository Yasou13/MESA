"""add durable downstream projection and reconciliation state.

Revision ID: a1d2e3f4b5c6
Revises: f8a6c0d1e2b3
"""
from typing import Sequence, Union
from alembic import op

revision: str = "a1d2e3f4b5c6"
down_revision: Union[str, Sequence[str], None] = "f8a6c0d1e2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in bind.dialect.get_columns(bind, "lancedb_wal")}
    additions = {
        "mutation_id": "TEXT",
        "idempotency_key": "TEXT",
        "vector_state": "TEXT NOT NULL DEFAULT 'PENDING'",
        "graph_state": "TEXT NOT NULL DEFAULT 'NOT_REQUIRED'",
        "reconciliation_state": "TEXT NOT NULL DEFAULT 'UNKNOWN_OR_UNVERIFIABLE'",
        "fence_epoch": "INTEGER NOT NULL DEFAULT 0",
        "retry_limit": "INTEGER NOT NULL DEFAULT 3",
        "reconciled_at": "TEXT",
    }
    for name, ddl in additions.items():
        if name not in existing:
            op.execute(f"ALTER TABLE lancedb_wal ADD COLUMN {name} {ddl}")
    op.execute("UPDATE lancedb_wal SET mutation_id = id WHERE mutation_id IS NULL")
    op.execute("UPDATE lancedb_wal SET idempotency_key = 'wal:' || id WHERE idempotency_key IS NULL")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_lancedb_wal_mutation_id ON lancedb_wal(mutation_id)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_lancedb_wal_idempotency_key ON lancedb_wal(idempotency_key)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_lancedb_wal_projection_recovery ON lancedb_wal(state, vector_state, graph_state, lease_expires_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_lancedb_wal_projection_recovery")
    op.execute("DROP INDEX IF EXISTS idx_lancedb_wal_idempotency_key")
    op.execute("DROP INDEX IF EXISTS idx_lancedb_wal_mutation_id")
