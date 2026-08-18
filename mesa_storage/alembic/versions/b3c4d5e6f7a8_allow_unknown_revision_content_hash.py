"""Allow a revision's legacy content hash to remain unknown.

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
"""

import sqlalchemy as sa
from alembic import op

revision = "b3c4d5e6f7a8"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite requires a table rebuild to remove NOT NULL.  Alembic's batch
    # operation preserves existing rows, constraints and indexes while making
    # NULL the truthful representation for an undeclared whole-revision hash.
    with op.batch_alter_table("document_revisions", recreate="always") as batch_op:
        batch_op.alter_column(
            "content_hash",
            existing_type=sa.Text(),
            nullable=True,
        )


def downgrade() -> None:
    raise RuntimeError("unknown revision content hash migration is forward-only")
