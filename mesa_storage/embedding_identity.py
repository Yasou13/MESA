"""Explicit offline adoption of legacy vector embedding provenance."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from mesa_storage.writer_lock import StorageWriterLock

_ACTIVE_VECTOR_SOURCE = """
    EXISTS (
        SELECT 1
        FROM artifact_sources s
        JOIN artifact_registry r ON r.registry_id = s.registry_id
        WHERE s.mutation_id = memory_mutations.mutation_id
          AND s.state = 'ACTIVE'
          AND r.state = 'ACTIVE'
          AND r.store_name = 'VECTOR'
          AND r.artifact_kind = 'ENTITY_VECTOR'
    )
"""


class EmbeddingIdentityAdoptionError(RuntimeError):
    """Legacy provider provenance cannot be adopted without ambiguity."""


def _validate_storage_boundary(
    *, trusted_root: Path, storage_root: Path, writer_lock: StorageWriterLock
) -> Path:
    try:
        trusted = trusted_root.resolve(strict=True)
        storage = storage_root.resolve(strict=True)
    except OSError as exc:
        raise EmbeddingIdentityAdoptionError(
            "adoption storage path is unavailable"
        ) from exc
    if (
        trusted_root.is_symlink()
        or storage_root.is_symlink()
        or storage == trusted
        or not storage.is_relative_to(trusted)
    ):
        raise EmbeddingIdentityAdoptionError(
            "adoption storage path escapes trusted root"
        )
    trusted_absolute = trusted_root.absolute()
    current = storage_root.absolute()
    while current != trusted_absolute:
        if current.is_symlink() or current.parent == current:
            raise EmbeddingIdentityAdoptionError(
                "adoption storage path contains a symlink"
            )
        current = current.parent
    if writer_lock.released or writer_lock.storage_root != storage:
        raise EmbeddingIdentityAdoptionError(
            "adoption requires the storage writer lock"
        )
    database = storage / "mesa.db"
    if not database.is_file() or database.is_symlink():
        raise EmbeddingIdentityAdoptionError("canonical SQLite is unavailable")
    return database


def adopt_legacy_embedding_identity(
    *,
    trusted_root: Path,
    storage_root: Path,
    writer_lock: StorageWriterLock,
    provider: str,
    model: str,
    version: str,
    dimension: int,
) -> int:
    """Fill missing active-vector identity after operator assertion.

    Existing non-null fields are immutable evidence: every one must match the
    asserted identity. The command is deliberately separate from projection
    rebuild so ``mesa-v4-rebuild run`` never mutates canonical SQLite.
    """
    if not provider or not model or not version or dimension < 1:
        raise EmbeddingIdentityAdoptionError("complete embedding identity is required")
    database = _validate_storage_boundary(
        trusted_root=trusted_root,
        storage_root=storage_root,
        writer_lock=writer_lock,
    )
    expected = (provider, model, version, dimension)
    connection = sqlite3.connect(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        operations = int(
            connection.execute(
                "SELECT COUNT(*) FROM system_operations "
                "WHERE operation_kind = 'PROJECTION_REBUILD' "
                "AND state IN ('PENDING', 'RETRYABLE_FAILED')"
            ).fetchone()[0]
        )
        if operations != 1:
            raise EmbeddingIdentityAdoptionError(
                "adoption requires one maintenance-pending rebuild"
            )
        signatures = connection.execute(
            "SELECT DISTINCT embedding_provider, embedding_model, "
            "embedding_version, embedding_dimension FROM memory_mutations "
            f"WHERE {_ACTIVE_VECTOR_SOURCE}"
        ).fetchall()
        for signature in signatures:
            for current, asserted in zip(signature, expected):
                if current is not None and current != asserted:
                    raise EmbeddingIdentityAdoptionError(
                        "legacy embedding identity conflicts with assertion"
                    )
        connection.execute(
            "UPDATE memory_mutations SET "
            "embedding_provider = COALESCE(embedding_provider, ?), "
            "embedding_model = COALESCE(embedding_model, ?), "
            "embedding_version = COALESCE(embedding_version, ?), "
            "embedding_dimension = COALESCE(embedding_dimension, ?), "
            "updated_at = CURRENT_TIMESTAMP "
            f"WHERE {_ACTIVE_VECTOR_SOURCE} AND ("
            "embedding_provider IS NULL OR embedding_model IS NULL OR "
            "embedding_version IS NULL OR embedding_dimension IS NULL)",
            expected,
        )
        updated = int(connection.execute("SELECT changes()").fetchone()[0])
        verified = connection.execute(
            "SELECT DISTINCT embedding_provider, embedding_model, "
            "embedding_version, embedding_dimension FROM memory_mutations "
            f"WHERE {_ACTIVE_VECTOR_SOURCE}"
        ).fetchall()
        if any(tuple(signature) != expected for signature in verified):
            raise EmbeddingIdentityAdoptionError("embedding identity adoption failed")
        connection.commit()
        return updated
    except EmbeddingIdentityAdoptionError:
        connection.rollback()
        raise
    except sqlite3.DatabaseError as exc:
        connection.rollback()
        raise EmbeddingIdentityAdoptionError(
            "embedding identity adoption failed"
        ) from exc
    finally:
        connection.close()
