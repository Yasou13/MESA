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
    # `content_hash` predates declared whole-revision hashes.  In particular,
    # direct chunk insertion historically populated it from a chunk payload,
    # so its provenance is not uniformly a caller declaration.  Preserve the
    # column for compatibility but leave the new semantic field unknown.
    op.execute("ALTER TABLE document_revisions ADD COLUMN declared_content_hash TEXT")


def downgrade() -> None:
    raise RuntimeError("declared content hash migration is forward-only")
