"""Drop legacy V3 node store, purge journal, LanceDB WAL and system_config tables.

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
"""

from alembic import op

revision = "f7a8b9c0d1e2"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for trigger in (
        "trg_nodes_fts_insert",
        "trg_nodes_fts_delete",
        "trg_nodes_fts_update",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")

    op.execute("DROP TABLE IF EXISTS nodes_fts")

    for index in (
        "idx_nodes_active",
        "idx_nodes_entity_name",
        "idx_nodes_agent",
        "idx_nodes_unconsolidated",
        "idx_nodes_soft_deleted",
    ):
        op.execute(f"DROP INDEX IF EXISTS {index}")

    op.execute("DROP TABLE IF EXISTS nodes")
    op.execute("DROP TABLE IF EXISTS lancedb_wal")
    op.execute("DROP TABLE IF EXISTS purge_journal")
    op.execute("DROP TABLE IF EXISTS system_config")


def downgrade() -> None:
    # MESA migrations are forward-only (alembic/env.py blocks downgrade).
    pass
