"""add_mcp_policy_rules

Revision ID: 9c6c7ae69ed7
Revises: a94df5d14fce
Create Date: 2026-07-24 22:47:55.820283

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c6c7ae69ed7"
down_revision: Union[str, Sequence[str], None] = "a94df5d14fce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "mcp_policy_rules",
        sa.Column("rule_id", sa.String(), nullable=False),
        sa.Column("scope_type", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(), nullable=True),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("effect", sa.String(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("conditions_json", sa.String(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("rule_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("mcp_policy_rules")
