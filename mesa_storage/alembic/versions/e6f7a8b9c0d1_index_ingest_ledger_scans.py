"""Index aggregate pipeline and revision completeness lookups.

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
"""

from alembic import op

revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_memory_mutations_pipeline_state",
        "memory_mutations",
        ["pipeline_run_id", "state"],
    )
    op.create_index(
        "idx_memory_mutations_revision_chunk_state",
        "memory_mutations",
        ["revision_id", "chunk_id", "state"],
    )
    op.create_index(
        "idx_source_chunks_revision_chunk", "source_chunks", ["revision_id", "chunk_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_source_chunks_revision_chunk", table_name="source_chunks")
    op.drop_index(
        "idx_memory_mutations_revision_chunk_state", table_name="memory_mutations"
    )
    op.drop_index("idx_memory_mutations_pipeline_state", table_name="memory_mutations")
