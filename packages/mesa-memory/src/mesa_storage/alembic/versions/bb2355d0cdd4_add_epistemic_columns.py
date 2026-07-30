"""add_epistemic_columns

Revision ID: bb2355d0cdd4
Revises: 4933fb5fd0ea
Create Date: 2026-07-13 09:02:18.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bb2355d0cdd4"
down_revision: Union[str, None] = "4933fb5fd0ea"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add confidence and is_quarantined to nodes table
    op.add_column(
        "nodes",
        sa.Column("confidence", sa.Float(), server_default="1.0", nullable=False),
    )
    op.add_column(
        "nodes",
        sa.Column("is_quarantined", sa.Boolean(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    # SQLite does not support dropping columns easily in some older versions,
    # but Alembic handles it using batch_alter_table.
    with op.batch_alter_table("nodes") as batch_op:
        batch_op.drop_column("is_quarantined")
        batch_op.drop_column("confidence")
