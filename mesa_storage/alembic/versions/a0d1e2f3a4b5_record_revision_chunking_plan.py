"""Record the chunking mode selected for whole-document admission."""

from alembic import op

revision = "a0d1e2f3a4b5"
down_revision = "f9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE document_revisions ADD COLUMN chunking_plan TEXT "
        "CHECK (chunking_plan IN ('auto', 'plain'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE document_revisions DROP COLUMN chunking_plan")
