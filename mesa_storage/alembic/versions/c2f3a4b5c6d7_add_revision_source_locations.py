"""Store PDF page and XLSX row locations with the document revision."""

import sqlalchemy as sa
from alembic import op

revision = "c2f3a4b5c6d7"
down_revision = "b1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_revisions",
        sa.Column("source_locations_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    raise NotImplementedError("Document location migration is forward-only.")
