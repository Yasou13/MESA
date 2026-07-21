"""add durable session finalization journal.

Revision ID: b2e3f4a5c6d7
Revises: a1d2e3f4b5c6
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b2e3f4a5c6d7"
down_revision: Union[str, Sequence[str], None] = "a1d2e3f4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE IF NOT EXISTS session_finalization_journal (
        finalization_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, session_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE, state TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
        retry_limit INTEGER NOT NULL DEFAULT 3, claim_token TEXT, claimed_by TEXT, lease_expires_at TEXT,
        last_error_class TEXT, completed_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(agent_id, session_id)
    )""")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_finalization_recovery ON session_finalization_journal(state, lease_expires_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_session_finalization_recovery")
    op.execute("DROP TABLE IF EXISTS session_finalization_journal")
