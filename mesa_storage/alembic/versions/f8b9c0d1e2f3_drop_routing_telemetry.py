"""Drop legacy consolidation routing_telemetry table.

Revision ID: f8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-11-23 00:00:00.000000
"""

from alembic import op

revision = "f8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_routing_telemetry_agent_created;")
    op.execute("DROP TABLE IF EXISTS routing_telemetry;")


def downgrade() -> None:
    # Forward-only migration: legacy consolidation telemetry is removed (D69).
    pass
