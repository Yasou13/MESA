"""Add char_start and char_end citation spans to source_chunks (D95)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b1e2f3a4b5c6"
down_revision = "a0d1e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE source_chunks ADD COLUMN char_start INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE source_chunks ADD COLUMN char_end INTEGER NOT NULL DEFAULT 0"
    )
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT chunk_id, revision_id, content_payload "
            "FROM source_chunks ORDER BY revision_id, ordinal, chunk_id"
        )
    ).fetchall()
    current_rev: str | None = None
    offset = 0
    updates: list[dict[str, object]] = []
    for chunk_id, revision_id, content_payload in rows:
        rev_key = str(revision_id)
        if rev_key != current_rev:
            current_rev = rev_key
            offset = 0
        text = str(content_payload or "")
        start = offset
        end = start + len(text)
        offset = end
        updates.append({"cid": str(chunk_id), "s": start, "e": end})
    if updates:
        conn.execute(
            sa.text(
                "UPDATE source_chunks SET char_start = :s, char_end = :e "
                "WHERE chunk_id = :cid"
            ),
            updates,
        )


def downgrade() -> None:
    op.execute("ALTER TABLE source_chunks DROP COLUMN char_end")
    op.execute("ALTER TABLE source_chunks DROP COLUMN char_start")
