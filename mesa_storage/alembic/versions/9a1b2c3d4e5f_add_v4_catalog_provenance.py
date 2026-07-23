"""add v4 catalog, canonical artifacts, provenance and pipeline runs.

Revision ID: 9a1b2c3d4e5f
Revises: d4e5f6a7b8c9
"""

from typing import Sequence, Union

from alembic import op

revision: str = "9a1b2c3d4e5f"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE memory_mutations ADD COLUMN workspace_id TEXT")
    op.execute("ALTER TABLE memory_mutations ADD COLUMN dataset_id TEXT")
    op.execute("ALTER TABLE memory_mutations ADD COLUMN document_id TEXT")
    op.execute("ALTER TABLE memory_mutations ADD COLUMN revision_id TEXT")
    op.execute("ALTER TABLE memory_mutations ADD COLUMN chunk_id TEXT")
    op.execute("ALTER TABLE memory_mutations ADD COLUMN source_ref TEXT")
    op.execute("ALTER TABLE memory_mutations ADD COLUMN evidence_span TEXT NOT NULL DEFAULT ''")
    op.execute(
        """CREATE TABLE IF NOT EXISTS tenants (
            tenant_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS workspaces (
            workspace_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(tenant_id, name)
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS datasets (
            dataset_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
            workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(workspace_id, name)
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS documents (
            document_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
            dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id),
            external_ref TEXT,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(dataset_id, external_ref)
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS document_revisions (
            revision_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
            document_id TEXT NOT NULL REFERENCES documents(document_id),
            revision_number INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            supersedes_revision_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(document_id, revision_number),
            UNIQUE(document_id, content_hash)
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS source_chunks (
            chunk_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
            dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id),
            revision_id TEXT NOT NULL REFERENCES document_revisions(revision_id),
            ordinal INTEGER NOT NULL,
            content_payload TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(revision_id, ordinal)
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS v4_sessions (
            session_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
            workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
            agent_id TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ended_at TEXT
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS v4_session_datasets (
            session_id TEXT NOT NULL REFERENCES v4_sessions(session_id),
            dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id),
            PRIMARY KEY(session_id, dataset_id)
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS v4_entities (
            entity_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
            entity_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            ontology_uri TEXT,
            identity_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            redirect_entity_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(tenant_id, entity_type, identity_key)
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS entity_aliases (
            tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
            entity_id TEXT NOT NULL REFERENCES v4_entities(entity_id),
            entity_type TEXT NOT NULL,
            alias TEXT NOT NULL,
            normalized_alias TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(tenant_id, entity_type, normalized_alias)
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS entity_external_ids (
            tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
            entity_id TEXT NOT NULL REFERENCES v4_entities(entity_id),
            entity_type TEXT NOT NULL,
            scheme TEXT NOT NULL,
            external_id TEXT NOT NULL,
            normalized_external_id TEXT NOT NULL,
            is_primary INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(tenant_id, entity_type, scheme, normalized_external_id)
        )"""
    )
    op.execute(
        """CREATE VIRTUAL TABLE IF NOT EXISTS v4_entities_fts
           USING fts5(canonical_name, entity_type,
                      content='v4_entities', content_rowid='rowid')"""
    )
    op.execute(
        """CREATE TRIGGER IF NOT EXISTS trg_v4_entities_fts_insert
           AFTER INSERT ON v4_entities BEGIN
             INSERT INTO v4_entities_fts(rowid, canonical_name, entity_type)
             VALUES (NEW.rowid, NEW.canonical_name, NEW.entity_type);
           END"""
    )
    op.execute(
        """CREATE TRIGGER IF NOT EXISTS trg_v4_entities_fts_delete
           AFTER DELETE ON v4_entities BEGIN
             INSERT INTO v4_entities_fts(v4_entities_fts, rowid,
                                         canonical_name, entity_type)
             VALUES ('delete', OLD.rowid, OLD.canonical_name, OLD.entity_type);
           END"""
    )
    op.execute(
        """CREATE TRIGGER IF NOT EXISTS trg_v4_entities_fts_update
           AFTER UPDATE ON v4_entities BEGIN
             INSERT INTO v4_entities_fts(v4_entities_fts, rowid,
                                         canonical_name, entity_type)
             VALUES ('delete', OLD.rowid, OLD.canonical_name, OLD.entity_type);
             INSERT INTO v4_entities_fts(rowid, canonical_name, entity_type)
             VALUES (NEW.rowid, NEW.canonical_name, NEW.entity_type);
           END"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS pipeline_runs (
            pipeline_run_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            workspace_id TEXT,
            dataset_id TEXT,
            session_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            principal_id TEXT,
            state TEXT NOT NULL,
            failure_class TEXT,
            claim_token TEXT,
            claimed_by TEXT,
            lease_expires_at TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            retry_limit INTEGER NOT NULL DEFAULT 5,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS pipeline_run_events (
            event_id TEXT PRIMARY KEY,
            pipeline_run_id TEXT NOT NULL REFERENCES pipeline_runs(pipeline_run_id),
            from_state TEXT,
            to_state TEXT NOT NULL,
            event_type TEXT NOT NULL,
            detail_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS v4_assertions (
            assertion_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            dataset_id TEXT NOT NULL,
            subject_id TEXT NOT NULL REFERENCES v4_entities(entity_id),
            predicate TEXT NOT NULL,
            object_entity_id TEXT REFERENCES v4_entities(entity_id),
            literal_value TEXT,
            source_ref TEXT NOT NULL,
            document_id TEXT NOT NULL,
            revision_id TEXT NOT NULL,
            chunk_id TEXT NOT NULL,
            evidence_span TEXT NOT NULL DEFAULT '',
            jurisdiction TEXT NOT NULL DEFAULT '',
            authority_level TEXT NOT NULL DEFAULT '',
            valid_from TEXT NOT NULL DEFAULT '',
            valid_to TEXT NOT NULL DEFAULT '',
            observed_at TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 1.0,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            mutation_id TEXT NOT NULL REFERENCES memory_mutations(mutation_id),
            pipeline_run_id TEXT NOT NULL REFERENCES pipeline_runs(pipeline_run_id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK ((object_entity_id IS NOT NULL) != (literal_value IS NOT NULL))
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS v4_assertion_links (
            source_assertion_id TEXT NOT NULL REFERENCES v4_assertions(assertion_id),
            target_assertion_id TEXT NOT NULL REFERENCES v4_assertions(assertion_id),
            relation_type TEXT NOT NULL,
            mutation_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(source_assertion_id, target_assertion_id, relation_type)
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS artifact_registry (
            registry_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            dataset_id TEXT,
            store_name TEXT NOT NULL,
            artifact_kind TEXT NOT NULL,
            physical_artifact_id TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'ACTIVE',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            invalidated_at TEXT,
            UNIQUE(tenant_id, agent_id, store_name, artifact_kind, physical_artifact_id)
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS artifact_sources (
            source_ownership_id TEXT PRIMARY KEY,
            registry_id TEXT NOT NULL REFERENCES artifact_registry(registry_id),
            mutation_id TEXT NOT NULL REFERENCES memory_mutations(mutation_id),
            pipeline_run_id TEXT,
            dataset_id TEXT,
            document_id TEXT,
            revision_id TEXT,
            chunk_id TEXT,
            source_ref TEXT NOT NULL DEFAULT '',
            evidence_span TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            invalidated_at TEXT,
            UNIQUE(registry_id, mutation_id, chunk_id)
        )"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS artifact_cleanup_outbox (
            cleanup_id TEXT PRIMARY KEY,
            pipeline_run_id TEXT NOT NULL REFERENCES pipeline_runs(pipeline_run_id),
            registry_id TEXT NOT NULL REFERENCES artifact_registry(registry_id),
            state TEXT NOT NULL DEFAULT 'PENDING',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            retry_limit INTEGER NOT NULL DEFAULT 5,
            claim_token TEXT,
            claimed_by TEXT,
            lease_expires_at TEXT,
            last_error_class TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(pipeline_run_id, registry_id)
        )"""
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_v4_entities_lookup "
        "ON v4_entities(tenant_id, entity_type, normalized_name, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_v4_assertions_retrieval "
        "ON v4_assertions(tenant_id, dataset_id, status, predicate)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_artifact_sources_owner "
        "ON artifact_sources(mutation_id, state)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_artifact_cleanup_claim "
        "ON artifact_cleanup_outbox(state, lease_expires_at, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_pipeline_runs_scope "
        "ON pipeline_runs(tenant_id, dataset_id, state, updated_at)"
    )

    # Preserve already-created V4 receipts as canonical physical artifacts and
    # ownership links. Legacy rows may not have dataset/document provenance;
    # those fields remain NULL until an offline rebuild supplies it.
    op.execute(
        """INSERT OR IGNORE INTO pipeline_runs
           (pipeline_run_id, tenant_id, session_id, agent_id, state, failure_class)
           SELECT DISTINCT COALESCE(pipeline_run_id, mutation_id), tenant_id,
                  session_id, agent_id,
                  CASE state
                    WHEN 'RECEIVED' THEN 'QUEUED'
                    WHEN 'SQL_APPLIED' THEN 'PROJECTING'
                    WHEN 'VECTOR_APPLIED' THEN 'PROJECTING'
                    WHEN 'GRAPH_APPLIED' THEN 'PROJECTING'
                    WHEN 'DEAD_LETTER' THEN 'DLQ'
                    ELSE state
                  END,
                  failure_class
           FROM memory_mutations"""
    )
    op.execute(
        """UPDATE memory_mutations
           SET pipeline_run_id = mutation_id
           WHERE pipeline_run_id IS NULL"""
    )
    op.execute(
        """INSERT OR IGNORE INTO artifact_registry
           (registry_id, tenant_id, agent_id, store_name, artifact_kind,
            physical_artifact_id, state, metadata_json, created_at, invalidated_at)
           SELECT m.tenant_id || '|' ||
                  CASE WHEN a.store_name = 'SQL' THEN '__tenant__' ELSE m.agent_id END ||
                  '|' || a.store_name || '|' ||
                  a.artifact_kind || '|' || a.artifact_id,
                  m.tenant_id,
                  CASE WHEN a.store_name = 'SQL' THEN '__tenant__' ELSE m.agent_id END,
                  a.store_name, a.artifact_kind, a.artifact_id,
                  a.state, a.metadata_json, a.created_at, a.invalidated_at
           FROM memory_artifacts a
           JOIN memory_mutations m ON m.mutation_id = a.mutation_id"""
    )
    op.execute(
        """INSERT OR IGNORE INTO artifact_sources
           (source_ownership_id, registry_id, mutation_id, pipeline_run_id,
            source_ref, state, created_at, invalidated_at)
           SELECT a.artifact_row_id,
                  m.tenant_id || '|' ||
                  CASE WHEN a.store_name = 'SQL' THEN '__tenant__' ELSE m.agent_id END ||
                  '|' || a.store_name || '|' ||
                  a.artifact_kind || '|' || a.artifact_id,
                  a.mutation_id, m.pipeline_run_id,
                  COALESCE(CAST(m.raw_log_id AS TEXT), ''),
                  a.state, a.created_at, a.invalidated_at
           FROM memory_artifacts a
           JOIN memory_mutations m ON m.mutation_id = a.mutation_id"""
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_pipeline_runs_scope")
    op.execute("DROP INDEX IF EXISTS idx_artifact_cleanup_claim")
    op.execute("DROP INDEX IF EXISTS idx_artifact_sources_owner")
    op.execute("DROP INDEX IF EXISTS idx_v4_assertions_retrieval")
    op.execute("DROP INDEX IF EXISTS idx_v4_entities_lookup")
    op.execute("DROP TABLE IF EXISTS artifact_cleanup_outbox")
    op.execute("DROP TABLE IF EXISTS artifact_sources")
    op.execute("DROP TABLE IF EXISTS artifact_registry")
    op.execute("DROP TABLE IF EXISTS v4_assertion_links")
    op.execute("DROP TABLE IF EXISTS v4_assertions")
    op.execute("DROP TABLE IF EXISTS pipeline_run_events")
    op.execute("DROP TABLE IF EXISTS pipeline_runs")
    op.execute("DROP TRIGGER IF EXISTS trg_v4_entities_fts_update")
    op.execute("DROP TRIGGER IF EXISTS trg_v4_entities_fts_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_v4_entities_fts_insert")
    op.execute("DROP TABLE IF EXISTS v4_entities_fts")
    op.execute("DROP TABLE IF EXISTS entity_external_ids")
    op.execute("DROP TABLE IF EXISTS entity_aliases")
    op.execute("DROP TABLE IF EXISTS v4_entities")
    op.execute("DROP TABLE IF EXISTS v4_session_datasets")
    op.execute("DROP TABLE IF EXISTS v4_sessions")
    op.execute("DROP TABLE IF EXISTS source_chunks")
    op.execute("DROP TABLE IF EXISTS document_revisions")
    op.execute("DROP TABLE IF EXISTS documents")
    op.execute("DROP TABLE IF EXISTS datasets")
    op.execute("DROP TABLE IF EXISTS workspaces")
    op.execute("DROP TABLE IF EXISTS tenants")
    op.execute("ALTER TABLE memory_mutations DROP COLUMN evidence_span")
    op.execute("ALTER TABLE memory_mutations DROP COLUMN source_ref")
    op.execute("ALTER TABLE memory_mutations DROP COLUMN chunk_id")
    op.execute("ALTER TABLE memory_mutations DROP COLUMN revision_id")
    op.execute("ALTER TABLE memory_mutations DROP COLUMN document_id")
    op.execute("ALTER TABLE memory_mutations DROP COLUMN dataset_id")
    op.execute("ALTER TABLE memory_mutations DROP COLUMN workspace_id")
