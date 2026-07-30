"""add deterministic payload byte accounting to durable dispatch queue.

Revision ID: f7e5b9c0d1a2
Revises: f6d4a7b8c9e0
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f7e5b9c0d1a2"
down_revision: Union[str, Sequence[str], None] = "f6d4a7b8c9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {
        column["name"] for column in bind.dialect.get_columns(bind, "dispatch_queue")
    }
    if "payload_bytes" not in existing:
        op.execute(
            "ALTER TABLE dispatch_queue ADD COLUMN payload_bytes INTEGER NOT NULL DEFAULT 0"
        )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_dispatch_queue_admission ON dispatch_queue(state, agent_id)"
    )


def downgrade() -> None:
    # SQLite column removal is intentionally avoided; only the additive index is reversible.
    op.execute("DROP INDEX IF EXISTS idx_dispatch_queue_admission")
