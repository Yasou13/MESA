"""Add declared whole-revision content hash column.

Revision ID: a2b3c4d5e6f7
Revises: 0a7b8c9d0e1f
"""

from alembic import op

revision = "a2b3c4d5e6f7"
down_revision = "0a7b8c9d0e1f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE document_revisions ADD COLUMN declared_content_hash TEXT"
    )
    op.execute(
        "UPDATE document_revisions SET declared_content_hash = content_hash "
        "WHERE content_hash != '' AND content_hash IS NOT NULL"
    )


def downgrade() -> None:
    raise RuntimeError("declared content hash migration is forward-only")
