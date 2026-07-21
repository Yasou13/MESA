"""add fenced durable dispatch completion receipts.

Revision ID: f8a6c0d1e2b3
Revises: f7e5b9c0d1a2
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f8a6c0d1e2b3"
down_revision: Union[str, Sequence[str], None] = "f7e5b9c0d1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {
        column["name"] for column in bind.dialect.get_columns(bind, "dispatch_queue")
    }
    for name, ddl in {
        "claim_token": "TEXT",
        "claimed_by": "TEXT",
        "lease_expires_at": "TEXT",
        "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        "last_error_class": "TEXT",
    }.items():
        if name not in existing:
            op.execute(f"ALTER TABLE dispatch_queue ADD COLUMN {name} {ddl}")
    op.execute("""CREATE TABLE IF NOT EXISTS dispatch_completion_receipts (
        receipt_id TEXT PRIMARY KEY, queue_record_id TEXT NOT NULL UNIQUE, dispatch_id TEXT NOT NULL UNIQUE,
        tenant_id TEXT NOT NULL, agent_id TEXT NOT NULL, worker_id TEXT NOT NULL, claim_token TEXT NOT NULL,
        outcome TEXT NOT NULL, side_effect_verified INTEGER NOT NULL, attempt_count INTEGER NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE, completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_dispatch_queue_claim ON dispatch_queue(state, lease_expires_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_dispatch_queue_claim")
    op.execute("DROP TABLE IF EXISTS dispatch_completion_receipts")
