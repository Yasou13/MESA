"""Drop legacy GRAPH projection outbox and artifact rows (D79).

Revision ID: f9c0d1e2f3a4
Revises: f8b9c0d1e2f3
Create Date: 2026-11-23 00:00:00.000000
"""

from alembic import op

revision = "f9c0d1e2f3a4"
down_revision = "f8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM projection_attempts WHERE projection_id IN "
        "(SELECT projection_id FROM projection_outbox WHERE projection_name = 'GRAPH')"
    )
    op.execute("DELETE FROM projection_outbox WHERE projection_name = 'GRAPH'")
    op.execute(
        "DELETE FROM artifact_sources WHERE registry_id IN "
        "(SELECT registry_id FROM artifact_registry WHERE store_name = 'GRAPH')"
    )
    op.execute(
        "DELETE FROM artifact_cleanup_outbox WHERE registry_id IN "
        "(SELECT registry_id FROM artifact_registry WHERE store_name = 'GRAPH')"
    )
    op.execute("DELETE FROM artifact_registry WHERE store_name = 'GRAPH'")
    op.execute("DELETE FROM memory_artifacts WHERE store_name = 'GRAPH'")
    op.execute(
        "UPDATE projection_generations SET graph_relative_path = 'graph_unused' "
        "WHERE generation_id = 'legacy' AND graph_relative_path != 'graph_unused'"
    )
    op.execute(
        "UPDATE memory_mutations SET state = 'COMMITTED', "
        "updated_at = CURRENT_TIMESTAMP "
        "WHERE state IN ('VECTOR_APPLIED', 'GRAPH_APPLIED') "
        "AND EXISTS (SELECT 1 FROM projection_outbox s "
        "WHERE s.mutation_id = memory_mutations.mutation_id "
        "AND s.projection_name = 'SQL' AND s.state = 'COMPLETED') "
        "AND EXISTS (SELECT 1 FROM projection_outbox v "
        "WHERE v.mutation_id = memory_mutations.mutation_id "
        "AND v.projection_name = 'VECTOR' AND v.state IN ('COMPLETED', 'SKIPPED'))"
    )


def downgrade() -> None:
    # Forward-only migration: the GRAPH projection lane is removed (D79).
    pass
