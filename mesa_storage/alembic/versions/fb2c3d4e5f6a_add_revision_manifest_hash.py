"""Persist a deterministic source-chunk manifest hash for each revision."""

from alembic import op

revision = "fb2c3d4e5f6a"
down_revision = "fa1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE document_revisions ADD COLUMN manifest_hash TEXT NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    raise RuntimeError("revision manifest migration is forward-only")
