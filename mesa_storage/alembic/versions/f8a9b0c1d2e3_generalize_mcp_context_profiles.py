"""generalize binding context profiles beyond Codex.

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
"""

from alembic import op

revision = "f8a9b0c1d2e3"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS mcp_binding_context_profiles (
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
    op.execute(
        """INSERT OR IGNORE INTO mcp_binding_context_profiles
           (binding_id, session_start_enabled, post_compact_enabled, max_records,
            max_tokens, memory_types_json, revision, created_at, updated_at)
           SELECT binding_id, session_start_enabled, post_compact_enabled, max_records,
                  max_tokens, memory_types_json, revision, created_at, updated_at
           FROM mcp_codex_profiles"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mcp_binding_context_profiles")
