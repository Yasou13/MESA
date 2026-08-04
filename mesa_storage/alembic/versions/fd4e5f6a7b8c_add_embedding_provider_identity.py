"""Persist embedding provider identity for fail-closed projection rebuilds.

Revision ID: fd4e5f6a7b8c
Revises: fc3d4e5f6a7b
"""

from alembic import op

revision = "fd4e5f6a7b8c"
down_revision = "fc3d4e5f6a7b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE memory_mutations ADD COLUMN embedding_provider TEXT")


def downgrade() -> None:
    raise RuntimeError("embedding provider identity migration is forward-only")
