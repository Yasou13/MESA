"""add_temporal_validity_to_nodes

Revision ID: 41402f316580
Revises: 087de6628c51
Create Date: 2026-07-25 18:12:17.489745

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '41402f316580'
down_revision: Union[str, Sequence[str], None] = '087de6628c51'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE nodes ADD COLUMN valid_from TEXT DEFAULT NULL")
    op.execute("ALTER TABLE nodes ADD COLUMN valid_to TEXT DEFAULT NULL")
    op.execute("CREATE INDEX idx_nodes_temporal ON nodes(valid_from, valid_to)")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS idx_nodes_temporal")
    op.execute("ALTER TABLE nodes DROP COLUMN valid_from")
    op.execute("ALTER TABLE nodes DROP COLUMN valid_to")
