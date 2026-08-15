"""Freeze revision manifests before aggregate activation.

Revision ID: ff6a7b8c9d0e
Revises: fe5f6a7b8c9d
"""

from alembic import op

revision = "ff6a7b8c9d0e"
down_revision = "fe5f6a7b8c9d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE document_revisions ADD COLUMN manifest_frozen_at TEXT")
    # Historical terminal revisions necessarily crossed the old implicit
    # finalization boundary.  A PENDING revision is deliberately not
    # backfilled: its current chunks cannot prove that all intended work was
    # registered, so it must be finalized explicitly after upgrade.
    op.execute(
        "UPDATE document_revisions SET manifest_frozen_at = CURRENT_TIMESTAMP "
        "WHERE manifest_hash IS NOT NULL AND manifest_hash != '' "
        "AND status IN ('ACTIVE', 'SUPERSEDED', 'ROLLED_BACK', 'PURGED')"
    )


def downgrade() -> None:
    raise RuntimeError("revision manifest freeze migration is forward-only")
