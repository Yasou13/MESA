"""Add provenance-backed legal identity to canonical assertions.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
"""

import sqlalchemy as sa
from alembic import op

revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("v4_assertions") as batch_op:
        batch_op.add_column(
            sa.Column("law_id", sa.Text(), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("statute_code", sa.Text(), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("article_id", sa.Text(), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("paragraph_id", sa.Text(), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("clause_id", sa.Text(), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("legal_version_id", sa.Text(), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column(
                "legal_identity_source", sa.Text(), nullable=False, server_default=""
            )
        )

    op.create_index(
        "idx_v4_assertions_legal_identity_scope",
        "v4_assertions",
        [
            "tenant_id",
            "dataset_id",
            "statute_code",
            "article_id",
            "legal_version_id",
            "status",
        ],
    )


def downgrade() -> None:
    op.drop_index("idx_v4_assertions_legal_identity_scope", table_name="v4_assertions")
    with op.batch_alter_table("v4_assertions") as batch_op:
        batch_op.drop_column("legal_identity_source")
        batch_op.drop_column("legal_version_id")
        batch_op.drop_column("clause_id")
        batch_op.drop_column("paragraph_id")
        batch_op.drop_column("article_id")
        batch_op.drop_column("statute_code")
        batch_op.drop_column("law_id")
