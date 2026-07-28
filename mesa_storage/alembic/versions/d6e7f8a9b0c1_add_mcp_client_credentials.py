"""add binding-scoped MCP client credentials.

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
"""

from alembic import op

revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE IF NOT EXISTS mcp_client_credentials (
        credential_id TEXT PRIMARY KEY,
        client_id TEXT NOT NULL,
        binding_id TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        token_prefix TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_used_at TEXT,
        revoked_at TEXT,
        FOREIGN KEY(client_id) REFERENCES mcp_clients(client_id),
        FOREIGN KEY(binding_id) REFERENCES mcp_project_bindings(binding_id)
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS idx_mcp_credentials_binding ON mcp_client_credentials(binding_id, status)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_mcp_credentials_binding")
    op.execute("DROP TABLE IF EXISTS mcp_client_credentials")
