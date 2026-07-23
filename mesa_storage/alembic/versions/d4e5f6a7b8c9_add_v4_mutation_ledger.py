"""add v4 mutation ledger and idempotent projection outbox.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS memory_mutations (
            mutation_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL UNIQUE,
            raw_log_id INTEGER UNIQUE,
            tenant_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            content_payload TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            source TEXT NOT NULL DEFAULT 'api',
            pipeline_run_id TEXT,
            extraction_version TEXT NOT NULL DEFAULT 'v4',
            embedding_model TEXT,
            embedding_version TEXT,
            embedding_dimension INTEGER,
            state TEXT NOT NULL,
            failure_class TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS memory_artifacts (
            artifact_row_id TEXT PRIMARY KEY,
            mutation_id TEXT NOT NULL REFERENCES memory_mutations(mutation_id),
            store_name TEXT NOT NULL,
            artifact_kind TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'ACTIVE',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            invalidated_at TEXT,
            UNIQUE(mutation_id, store_name, artifact_kind, artifact_id)
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS projection_outbox (
            projection_id TEXT PRIMARY KEY,
            mutation_id TEXT NOT NULL REFERENCES memory_mutations(mutation_id),
            projection_name TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'PENDING',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            retry_limit INTEGER NOT NULL DEFAULT 5,
            claim_token TEXT,
            claimed_by TEXT,
            lease_expires_at TEXT,
            last_error_class TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(mutation_id, projection_name)
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS projection_attempts (
            attempt_id TEXT PRIMARY KEY,
            projection_id TEXT NOT NULL REFERENCES projection_outbox(projection_id),
            attempt_number INTEGER NOT NULL,
            outcome TEXT NOT NULL,
            error_class TEXT,
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT,
            UNIQUE(projection_id, attempt_number)
        )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_mutations_scope_state "
        "ON memory_mutations(agent_id, session_id, state, updated_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_projection_outbox_claim "
        "ON projection_outbox(state, lease_expires_at, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_artifacts_mutation "
        "ON memory_artifacts(mutation_id, state)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_memory_artifacts_mutation")
    op.execute("DROP INDEX IF EXISTS idx_projection_outbox_claim")
    op.execute("DROP INDEX IF EXISTS idx_memory_mutations_scope_state")
    op.execute("DROP TABLE IF EXISTS projection_attempts")
    op.execute("DROP TABLE IF EXISTS projection_outbox")
    op.execute("DROP TABLE IF EXISTS memory_artifacts")
    op.execute("DROP TABLE IF EXISTS memory_mutations")
