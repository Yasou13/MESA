"""add_mcp_clients_and_bindings

Revision ID: 1e7a061f7f9e
Revises: 9a1b2c3d4e5f
Create Date: 2026-07-24 22:36:22.746880

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1e7a061f7f9e"
down_revision: Union[str, Sequence[str], None] = "9a1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "mcp_clients",
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("client_type", sa.String(), nullable=False),
        sa.Column("principal_id", sa.String(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("default_tenant_id", sa.String(), nullable=True),
        sa.Column("default_workspace_id", sa.String(), nullable=True),
        sa.Column("default_dataset_id", sa.String(), nullable=True),
        sa.Column("default_project_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("last_seen_at", sa.String(), nullable=True),
        sa.Column("metadata_json", sa.String(), nullable=False, server_default="{}"),
        sa.PrimaryKeyConstraint("client_id"),
    )

    op.create_table(
        "mcp_project_bindings",
        sa.Column("binding_id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=True),
        sa.Column("external_project_id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("dataset_id", sa.String(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("binding_id"),
        sa.ForeignKeyConstraint(["client_id"], ["mcp_clients.client_id"]),
        sa.UniqueConstraint(
            "client_id", "external_project_id", name="uq_mcp_bindings_client_project"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("mcp_project_bindings")
    op.drop_table("mcp_clients")
