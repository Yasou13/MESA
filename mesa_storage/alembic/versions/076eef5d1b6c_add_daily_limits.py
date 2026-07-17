"""add_daily_limits

Revision ID: 076eef5d1b6c
Revises: bb2355d0cdd4
Create Date: 2026-07-16 11:34:24.935459

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "076eef5d1b6c"
down_revision: Union[str, Sequence[str], None] = "bb2355d0cdd4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "daily_limits",
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("request_count", sa.Integer(), default=0),
        sa.PrimaryKeyConstraint("agent_id", "date"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("daily_limits")
