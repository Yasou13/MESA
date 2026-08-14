"""Separate tenant-scoped catalog IDs from global physical keys.

Revision ID: 0a7b8c9d0e1f
Revises: ff6a7b8c9d0e
"""

from alembic import op

revision = "0a7b8c9d0e1f"
down_revision = "ff6a7b8c9d0e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE v4_catalog_identities (
            tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
            kind TEXT NOT NULL CHECK (
                kind IN ('workspace', 'dataset', 'document', 'revision', 'chunk')
            ),
            external_id TEXT NOT NULL,
            physical_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (tenant_id, kind, external_id),
            UNIQUE (kind, physical_id)
        )""")
    for kind, table, identifier in (
        ("workspace", "workspaces", "workspace_id"),
        ("dataset", "datasets", "dataset_id"),
        ("document", "documents", "document_id"),
        ("revision", "document_revisions", "revision_id"),
        ("chunk", "source_chunks", "chunk_id"),
    ):
        op.execute(
            "INSERT INTO v4_catalog_identities "
            "(tenant_id, kind, external_id, physical_id) "
            f"SELECT tenant_id, '{kind}', {identifier}, {identifier} FROM {table}"
        )


def downgrade() -> None:
    raise RuntimeError("tenant-scoped catalog identity migration is forward-only")
