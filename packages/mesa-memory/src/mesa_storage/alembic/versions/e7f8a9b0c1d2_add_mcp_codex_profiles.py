"""add binding-scoped Codex context profiles.

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
"""

from alembic import op

revision = "e7f8a9b0c1d2"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS mcp_codex_profiles (
            binding_id TEXT PRIMARY KEY,
            session_start_enabled INTEGER NOT NULL DEFAULT 1,
            post_compact_enabled INTEGER NOT NULL DEFAULT 1,
            max_records INTEGER NOT NULL DEFAULT 8,
            max_tokens INTEGER NOT NULL DEFAULT 2500,
            memory_types_json TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(binding_id) REFERENCES mcp_project_bindings(binding_id)
        )"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mcp_codex_profiles")
