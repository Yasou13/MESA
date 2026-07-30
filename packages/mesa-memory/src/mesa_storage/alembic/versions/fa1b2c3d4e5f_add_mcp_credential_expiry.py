"""Add expiry to binding-scoped MCP credentials."""

from alembic import op

revision = "fa1b2c3d4e5f"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE mcp_client_credentials ADD COLUMN expires_at TEXT")


def downgrade() -> None:
    raise RuntimeError("credential expiry migration is forward-only")
