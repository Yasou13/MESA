"""replace daily-limit credential storage with verified principal subjects

Revision ID: c3d4e5f6a7b8
Revises: b2e3f4a5c6d7
Create Date: 2026-07-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2e3f4a5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Discard legacy credential-keyed counters and establish subject IDs.

    The former ``agent_id`` values may contain raw API credentials. They are
    deliberately not copied: the one-time counter reset removes the sensitive
    rows from the application database and future backups.
    """
    op.drop_table("daily_limits")
    op.create_table(
        "daily_limits",
        sa.Column("subject_id", sa.String(), nullable=False),
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("subject_id", "date"),
    )


def downgrade() -> None:
    """Restore an empty legacy shape; deleted credential rows stay deleted."""
    op.drop_table("daily_limits")
    op.create_table(
        "daily_limits",
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("agent_id", "date"),
    )
