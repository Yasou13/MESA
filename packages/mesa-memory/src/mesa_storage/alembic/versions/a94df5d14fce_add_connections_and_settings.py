"""add_connections_and_settings

Revision ID: a94df5d14fce
Revises: 1e7a061f7f9e
Create Date: 2026-07-24 22:42:19.945481

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a94df5d14fce"
down_revision: Union[str, Sequence[str], None] = "1e7a061f7f9e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "mcp_connections",
        sa.Column("connection_id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("transport", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("connected_at", sa.String(), nullable=False),
        sa.Column("disconnected_at", sa.String(), nullable=True),
        sa.Column("last_seen_at", sa.String(), nullable=False),
        sa.Column("remote_address_hash", sa.String(), nullable=True),
        sa.Column("protocol_version", sa.String(), nullable=True),
        sa.Column("client_version", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("connection_id"),
        sa.ForeignKeyConstraint(["client_id"], ["mcp_clients.client_id"]),
    )

    op.create_table(
        "control_plane_settings",
        sa.Column("setting_key", sa.String(), nullable=False),
        sa.Column("setting_value_json", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("updated_by", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("setting_key"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("control_plane_settings")
    op.drop_table("mcp_connections")
