"""Add durable assertion retrieval representations and passage FTS.

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
"""

import sqlalchemy as sa
from alembic import op

revision = "c4d5e6f7a8b9"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("v4_assertions") as batch_op:
        batch_op.add_column(
            sa.Column("object_type", sa.Text(), nullable=False, server_default="LEGACY")
        )
        batch_op.add_column(
            sa.Column(
                "representation_version",
                sa.Text(),
                nullable=False,
                server_default="legacy-v0",
            )
        )

    op.execute(
        "UPDATE v4_assertions SET object_type = CASE "
        "WHEN object_entity_id IS NOT NULL THEN 'ENTITY' ELSE 'LEGACY_LITERAL' END"
    )
    op.execute(
        "UPDATE artifact_registry SET metadata_json = "
        "json_set(COALESCE(NULLIF(metadata_json, ''), '{}'), "
        "'$.representation_version', 'legacy-v0') "
        "WHERE store_name = 'VECTOR' AND artifact_kind = 'ASSERTION_VECTOR' "
        "AND json_extract(COALESCE(NULLIF(metadata_json, ''), '{}'), "
        "'$.representation_version') IS NULL"
    )
    op.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS v4_assertions_fts USING fts5(
        assertion_id UNINDEXED,
        predicate,
        evidence_span,
        literal_value,
        source_ref,
        subject_name,
        object_name,
        chunk_payload,
        tokenize='unicode61 remove_diacritics 2'
        )""")
    op.execute("""CREATE TRIGGER IF NOT EXISTS trg_v4_assertions_fts_insert
        AFTER INSERT ON v4_assertions BEGIN
          INSERT INTO v4_assertions_fts(
            rowid, assertion_id, predicate, evidence_span, literal_value,
            source_ref, subject_name, object_name, chunk_payload
          ) VALUES (
            NEW.rowid, NEW.assertion_id, NEW.predicate, NEW.evidence_span,
            COALESCE(NEW.literal_value, ''), NEW.source_ref,
            COALESCE((SELECT canonical_name FROM v4_entities
                      WHERE entity_id = NEW.subject_id), ''),
            COALESCE((SELECT canonical_name FROM v4_entities
                      WHERE entity_id = NEW.object_entity_id), ''),
            COALESCE((SELECT content_payload FROM source_chunks
                      WHERE chunk_id = NEW.chunk_id), '')
          );
        END""")
    op.execute("""CREATE TRIGGER IF NOT EXISTS trg_v4_assertions_fts_delete
        AFTER DELETE ON v4_assertions BEGIN
          DELETE FROM v4_assertions_fts WHERE rowid = OLD.rowid;
        END""")
    op.execute("""CREATE TRIGGER IF NOT EXISTS trg_v4_assertions_fts_update
        AFTER UPDATE ON v4_assertions BEGIN
          DELETE FROM v4_assertions_fts WHERE rowid = OLD.rowid;
          INSERT INTO v4_assertions_fts(
            rowid, assertion_id, predicate, evidence_span, literal_value,
            source_ref, subject_name, object_name, chunk_payload
          ) VALUES (
            NEW.rowid, NEW.assertion_id, NEW.predicate, NEW.evidence_span,
            COALESCE(NEW.literal_value, ''), NEW.source_ref,
            COALESCE((SELECT canonical_name FROM v4_entities
                      WHERE entity_id = NEW.subject_id), ''),
            COALESCE((SELECT canonical_name FROM v4_entities
                      WHERE entity_id = NEW.object_entity_id), ''),
            COALESCE((SELECT content_payload FROM source_chunks
                      WHERE chunk_id = NEW.chunk_id), '')
          );
        END""")
    op.execute("""INSERT INTO v4_assertions_fts(
        rowid, assertion_id, predicate, evidence_span, literal_value,
        source_ref, subject_name, object_name, chunk_payload
        )
        SELECT a.rowid, a.assertion_id, a.predicate, a.evidence_span,
               COALESCE(a.literal_value, ''), a.source_ref,
               COALESCE(s.canonical_name, ''), COALESCE(o.canonical_name, ''),
               COALESCE(sc.content_payload, '')
        FROM v4_assertions a
        LEFT JOIN v4_entities s ON s.entity_id = a.subject_id
        LEFT JOIN v4_entities o ON o.entity_id = a.object_entity_id
        LEFT JOIN source_chunks sc ON sc.chunk_id = a.chunk_id""")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_v4_assertions_fts_update")
    op.execute("DROP TRIGGER IF EXISTS trg_v4_assertions_fts_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_v4_assertions_fts_insert")
    op.execute("DROP TABLE IF EXISTS v4_assertions_fts")
    op.execute(
        "UPDATE artifact_registry SET metadata_json = "
        "json_remove(COALESCE(NULLIF(metadata_json, ''), '{}'), "
        "'$.representation_version') "
        "WHERE store_name = 'VECTOR' AND artifact_kind = 'ASSERTION_VECTOR' "
        "AND json_extract(COALESCE(NULLIF(metadata_json, ''), '{}'), "
        "'$.representation_version') = 'legacy-v0'"
    )
    with op.batch_alter_table("v4_assertions") as batch_op:
        batch_op.drop_column("representation_version")
        batch_op.drop_column("object_type")
