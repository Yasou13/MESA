"""Add durable projection rebuild operations and generation pointers."""

from alembic import op

revision = "fc3d4e5f6a7b"
down_revision = "fb2c3d4e5f6a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE projection_generations (
            generation_id TEXT PRIMARY KEY,
            generation_kind TEXT NOT NULL,
            lifecycle_state TEXT NOT NULL,
            vector_relative_path TEXT NOT NULL UNIQUE,
            graph_relative_path TEXT NOT NULL UNIQUE,
            source_manifest_hash TEXT,
            provider_manifest_json TEXT NOT NULL DEFAULT '{}',
            created_by_operation_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            activated_at TEXT,
            retained_at TEXT,
            CHECK (generation_kind IN ('LEGACY', 'REBUILD')),
            CHECK (lifecycle_state IN ('STAGING', 'ACTIVE', 'RETAINED', 'FAILED')),
            CHECK (length(vector_relative_path) > 0),
            CHECK (substr(vector_relative_path, 1, 1) != '/'),
            CHECK (instr(vector_relative_path, '..') = 0),
            CHECK (instr(vector_relative_path, '\\') = 0),
            CHECK (length(graph_relative_path) > 0),
            CHECK (substr(graph_relative_path, 1, 1) != '/'),
            CHECK (instr(graph_relative_path, '..') = 0),
            CHECK (instr(graph_relative_path, '\\') = 0),
            CHECK (source_manifest_hash IS NULL OR length(source_manifest_hash) = 64),
            CHECK (json_valid(provider_manifest_json))
        )""")
    op.execute("""CREATE TABLE projection_runtime (
            runtime_id INTEGER PRIMARY KEY CHECK (runtime_id = 1),
            active_generation_id TEXT NOT NULL
                REFERENCES projection_generations(generation_id),
            previous_generation_id TEXT
                REFERENCES projection_generations(generation_id),
            fencing_token INTEGER NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (previous_generation_id IS NULL OR
                   previous_generation_id != active_generation_id)
        )""")
    op.execute("""CREATE TABLE system_operations (
            operation_id TEXT PRIMARY KEY,
            operation_kind TEXT NOT NULL,
            scope_kind TEXT NOT NULL,
            scope_key TEXT NOT NULL DEFAULT 'default',
            requested_by_principal_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            state TEXT NOT NULL,
            claimed_by TEXT,
            claim_token TEXT,
            fencing_token INTEGER NOT NULL DEFAULT 0,
            lease_expires_at TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            retry_limit INTEGER NOT NULL DEFAULT 3,
            progress_completed INTEGER NOT NULL DEFAULT 0,
            progress_total INTEGER NOT NULL DEFAULT 0,
            checkpoint_json TEXT NOT NULL DEFAULT '{}',
            source_manifest_hash TEXT,
            source_manifest_json TEXT NOT NULL DEFAULT '{}',
            source_generation_id TEXT
                REFERENCES projection_generations(generation_id),
            target_generation_id TEXT
                REFERENCES projection_generations(generation_id),
            last_error_class TEXT,
            last_error_code TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            claimed_at TEXT,
            started_at TEXT,
            verifying_at TEXT,
            ready_at TEXT,
            completed_at TEXT,
            cancelled_at TEXT,
            UNIQUE (operation_kind, scope_kind, scope_key, idempotency_key),
            CHECK (operation_kind = 'PROJECTION_REBUILD'),
            CHECK (scope_kind = 'STORAGE_ROOT'),
            CHECK (scope_key = 'default'),
            CHECK (length(requested_by_principal_id) BETWEEN 1 AND 128),
            CHECK (length(idempotency_key) BETWEEN 1 AND 128),
            CHECK (length(payload_hash) = 64),
            CHECK (state IN (
                'PENDING', 'CLAIMED', 'RUNNING', 'VERIFYING',
                'READY_TO_CUTOVER', 'COMPLETED', 'RETRYABLE_FAILED',
                'FINAL_FAILED', 'CANCELLED'
            )),
            CHECK (fencing_token >= 0),
            CHECK (attempt_count >= 0),
            CHECK (retry_limit BETWEEN 1 AND 20),
            CHECK (progress_completed >= 0),
            CHECK (progress_total >= progress_completed),
            CHECK (json_valid(checkpoint_json)),
            CHECK (json_valid(source_manifest_json)),
            CHECK (source_manifest_hash IS NULL OR length(source_manifest_hash) = 64)
        )""")
    op.execute("""CREATE TABLE system_operation_events (
            event_id TEXT PRIMARY KEY,
            operation_id TEXT NOT NULL
                REFERENCES system_operations(operation_id) ON DELETE RESTRICT,
            sequence_number INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            from_state TEXT,
            to_state TEXT NOT NULL,
            fencing_token INTEGER NOT NULL,
            attempt_count INTEGER NOT NULL,
            progress_completed INTEGER NOT NULL DEFAULT 0,
            progress_total INTEGER NOT NULL DEFAULT 0,
            checkpoint_hash TEXT,
            error_class TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (operation_id, sequence_number),
            CHECK (sequence_number >= 1),
            CHECK (length(event_type) BETWEEN 1 AND 64),
            CHECK (fencing_token >= 0),
            CHECK (attempt_count >= 0),
            CHECK (progress_completed >= 0),
            CHECK (progress_total >= progress_completed),
            CHECK (checkpoint_hash IS NULL OR length(checkpoint_hash) = 64)
        )""")

    op.execute(
        "CREATE INDEX idx_system_operations_claim "
        "ON system_operations(state, lease_expires_at, created_at)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_system_operations_active_rebuild "
        "ON system_operations(operation_kind, scope_kind, scope_key) "
        "WHERE state IN ('PENDING', 'CLAIMED', 'RUNNING', 'VERIFYING', "
        "'READY_TO_CUTOVER', 'RETRYABLE_FAILED')"
    )
    op.execute(
        "CREATE INDEX idx_system_operation_events_sequence "
        "ON system_operation_events(operation_id, sequence_number)"
    )
    op.execute(
        "CREATE INDEX idx_projection_generations_lifecycle "
        "ON projection_generations(lifecycle_state, created_at)"
    )
    op.execute("""CREATE TRIGGER trg_system_operation_events_no_update
        BEFORE UPDATE ON system_operation_events
        BEGIN
            SELECT RAISE(ABORT, 'system operation events are append-only');
        END""")
    op.execute("""CREATE TRIGGER trg_system_operation_events_no_delete
        BEFORE DELETE ON system_operation_events
        BEGIN
            SELECT RAISE(ABORT, 'system operation events are append-only');
        END""")

    op.execute("""INSERT INTO projection_generations (
            generation_id, generation_kind, lifecycle_state,
            vector_relative_path, graph_relative_path, provider_manifest_json,
            activated_at
        ) VALUES (
            'legacy', 'LEGACY', 'ACTIVE', 'vector.lance', 'kuzu_db', '{}',
            CURRENT_TIMESTAMP
        )""")
    op.execute("""INSERT INTO projection_runtime (
            runtime_id, active_generation_id, previous_generation_id,
            fencing_token
        ) VALUES (1, 'legacy', NULL, 0)""")


def downgrade() -> None:
    raise RuntimeError("durable projection operation migration is forward-only")
