"""add durable raw-log dispatch journal and queue receipt.

Revision ID: f6d4a7b8c9e0
Revises: e9b7c3a1d4f2
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f6d4a7b8c9e0"
down_revision: Union[str, Sequence[str], None] = "e9b7c3a1d4f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""CREATE TABLE IF NOT EXISTS dispatch_journal (
        dispatch_id TEXT PRIMARY KEY, source_record_id INTEGER NOT NULL UNIQUE,
        tenant_id TEXT NOT NULL, agent_id TEXT NOT NULL, job_type TEXT NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
        attempt_count INTEGER NOT NULL DEFAULT 0, claim_token TEXT, claimed_by TEXT,
        lease_expires_at TEXT, last_error TEXT, queue_record_id TEXT,
        dispatched_at TEXT, finalized_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS dispatch_queue (
        queue_record_id TEXT PRIMARY KEY, dispatch_id TEXT NOT NULL UNIQUE,
        tenant_id TEXT NOT NULL, agent_id TEXT NOT NULL, job_type TEXT NOT NULL,
        payload_reference INTEGER NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL DEFAULT 'ENQUEUED', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS dispatch_receipts (
        receipt_id TEXT PRIMARY KEY, dispatch_id TEXT NOT NULL UNIQUE,
        queue_record_id TEXT NOT NULL UNIQUE, tenant_id TEXT NOT NULL, agent_id TEXT NOT NULL,
        outcome TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_dispatch_journal_recovery ON dispatch_journal(state, lease_expires_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_dispatch_journal_recovery")
    op.execute("DROP TABLE IF EXISTS dispatch_receipts")
    op.execute("DROP TABLE IF EXISTS dispatch_queue")
    op.execute("DROP TABLE IF EXISTS dispatch_journal")
