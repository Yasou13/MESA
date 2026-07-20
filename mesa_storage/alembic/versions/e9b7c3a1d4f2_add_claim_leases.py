"""add fenced raw-log and LanceDB WAL claim leases.

Revision ID: e9b7c3a1d4f2
Revises: c4f1a8e2d9b0
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e9b7c3a1d4f2"
down_revision: Union[str, Sequence[str], None] = "c4f1a8e2d9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_columns(table: str, columns: dict[str, str]) -> None:
    bind = op.get_bind()
    existing = {column["name"] for column in bind.dialect.get_columns(bind, table)}
    for name, ddl in columns.items():
        if name not in existing:
            op.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def upgrade() -> None:
    """Add durable ownership metadata without deleting existing work."""
    _add_columns("raw_logs", {
        "claim_token": "TEXT", "claimed_by": "TEXT", "lease_expires_at": "TEXT",
        "attempt_count": "INTEGER NOT NULL DEFAULT 0", "last_error": "TEXT", "processed_at": "TEXT",
    })
    _add_columns("lancedb_wal", {
        "state": "TEXT NOT NULL DEFAULT 'PENDING'", "claim_token": "TEXT", "claimed_by": "TEXT",
        "lease_expires_at": "TEXT", "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        "last_error": "TEXT", "acknowledged_at": "TEXT",
    })
    op.execute("CREATE INDEX IF NOT EXISTS idx_raw_logs_claim_recovery ON raw_logs(status, lease_expires_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_lancedb_wal_replay ON lancedb_wal(state, lease_expires_at)")


def downgrade() -> None:
    """Remove indexes only; SQLite column removal is intentionally not destructive."""
    op.execute("DROP INDEX IF EXISTS idx_lancedb_wal_replay")
    op.execute("DROP INDEX IF EXISTS idx_raw_logs_claim_recovery")
