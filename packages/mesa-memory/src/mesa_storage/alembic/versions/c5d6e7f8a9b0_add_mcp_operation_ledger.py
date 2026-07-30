"""add durable MCP operation and V4 idempotency ledgers.

Revision ID: c5d6e7f8a9b0
Revises: 41402f316580
"""

from alembic import op

revision = "c5d6e7f8a9b0"
down_revision = "41402f316580"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE IF NOT EXISTS mcp_operations (
        operation_id TEXT PRIMARY KEY,
        client_id TEXT NOT NULL,
        binding_id TEXT NOT NULL,
        connection_id TEXT,
        tool_name TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        payload_encrypted BLOB NOT NULL,
        status TEXT NOT NULL,
        approval_id TEXT,
        mutation_id TEXT,
        response_json TEXT,
        error_code TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at TEXT,
        UNIQUE(client_id, binding_id, tool_name, idempotency_key)
    )""")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_mcp_operations_status ON mcp_operations(status, updated_at)"
    )
    op.execute("""CREATE TABLE IF NOT EXISTS v4_idempotency_receipts (
        tenant_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        dataset_id TEXT NOT NULL,
        operation_type TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        state TEXT NOT NULL,
        mutation_id TEXT,
        response_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(tenant_id, agent_id, dataset_id, operation_type, idempotency_key)
    )""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS v4_idempotency_receipts")
    op.execute("DROP INDEX IF EXISTS idx_mcp_operations_status")
    op.execute("DROP TABLE IF EXISTS mcp_operations")
