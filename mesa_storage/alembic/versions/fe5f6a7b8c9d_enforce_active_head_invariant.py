"""Enforce active-head invariant for document revisions.

Revision ID: fe5f6a7b8c9d
Revises: fd4e5f6a7b8c
"""

from alembic import op

revision = "fe5f6a7b8c9d"
down_revision = "fd4e5f6a7b8c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Resolve pre-existing duplicate ACTIVE heads deterministically
    op.execute(
        "UPDATE document_revisions SET status = 'SUPERSEDED' "
        "WHERE status = 'ACTIVE' AND revision_id NOT IN ("
        "  SELECT revision_id FROM ("
        "    SELECT revision_id, ROW_NUMBER() OVER ("
        "      PARTITION BY document_id ORDER BY created_at DESC, revision_number DESC"
        "    ) as rn FROM document_revisions WHERE status = 'ACTIVE'"
        "  ) WHERE rn = 1"
        ")"
    )
    # Ensure the partial unique index on ACTIVE document heads exists
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_active_document_revision "
        "ON document_revisions(document_id) WHERE status = 'ACTIVE'"
    )


def downgrade() -> None:
    raise RuntimeError("active-head invariant migration is forward-only")
