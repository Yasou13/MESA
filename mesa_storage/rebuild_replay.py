"""Deterministic, resumable replay of vector projections."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from mesa_storage.projection_generations import (
    resolve_projection_generation_paths,
)
from mesa_storage.rebuild_preparation import (
    RebuildPreparation,
    RebuildSourceChangedError,
    canonical_sqlite_manifest,
)
from mesa_storage.repositories.operations import OperationRepositoryPort
from mesa_storage.vector_engine import EmbeddingProvider, VectorEngine

_LANES = ("vector",)


class RebuildReplayError(RuntimeError):
    """Base class for stable, content-free replay failures."""


class EmbeddingProviderConflictError(RebuildReplayError):
    """The configured embedding runtime cannot reproduce canonical vectors."""


class ProjectionOwnershipError(RebuildReplayError):
    """Canonical registry ownership is incomplete or crosses a trust scope."""


class RebuildCheckpointError(RebuildReplayError):
    """A durable replay checkpoint cannot be safely resumed."""


class RebuildInterruptedError(RebuildReplayError):
    """The runner was asked to stop at a durable batch boundary."""


def _staging_bytes(*roots: Path) -> int:
    total = 0
    for root in roots:
        if not root.exists():
            continue
        if root.is_symlink():
            raise RebuildReplayError("staging generation contains a symlink")
        if root.is_file():
            total += root.stat().st_size
            continue
        for item in root.rglob("*"):
            if item.is_symlink():
                raise RebuildReplayError("staging generation contains a symlink")
            if item.is_file():
                total += item.stat().st_size
    return total


class VectorReplayTarget(Protocol):
    @property
    def uri(self) -> str: ...

    async def initialize(self) -> None: ...

    async def compute_embedding_batch(self, texts: list[str]) -> list[list[float]]: ...

    async def bulk_upsert(self, records: list[dict[str, Any]]) -> int: ...

    async def close(self) -> None: ...


@dataclass(frozen=True)
class RebuildReplayResult:
    operation: dict[str, Any]
    counts: dict[str, int]
    completed: int
    total: int


_ACTIVE_OWNERSHIP = (
    "r.state = 'ACTIVE' AND EXISTS (SELECT 1 FROM artifact_sources s "
    "WHERE s.registry_id = r.registry_id AND s.state = 'ACTIVE')"
)
_VECTOR_PAYLOAD_TEXT_EXPR = (
    "CASE WHEN trim(a.evidence_span) != '' THEN "
    "trim(subject.canonical_name || ' ' || a.predicate || ' ' || "
    "COALESCE(object_entity.canonical_name, a.literal_value, '')) || "
    "': ' || trim(a.evidence_span) "
    "ELSE "
    "trim(subject.canonical_name || ' ' || a.predicate || ' ' || "
    "COALESCE(object_entity.canonical_name, a.literal_value, '')) "
    "END"
)
_VECTOR_QUERY = f"""
    SELECT DISTINCT r.tenant_id AS tenant_id, r.agent_id AS agent_id,
           a.assertion_id AS assertion_id,
           {_VECTOR_PAYLOAD_TEXT_EXPR} AS payload_text
    FROM artifact_registry r
    JOIN v4_assertions a ON a.assertion_id = r.physical_artifact_id
                        AND a.tenant_id = r.tenant_id
    JOIN v4_entities subject ON subject.entity_id = a.subject_id
    LEFT JOIN v4_entities object_entity ON object_entity.entity_id = a.object_entity_id
    WHERE {_ACTIVE_OWNERSHIP}
      AND r.store_name = 'VECTOR' AND r.artifact_kind = 'ASSERTION_VECTOR'
    ORDER BY tenant_id, agent_id, assertion_id
"""
_QUERIES = {
    "vector": _VECTOR_QUERY,
}


class ProjectionSnapshot:
    """Bounded read model over one immutable backup SQLite snapshot."""

    def __init__(self, database: Path) -> None:
        self._database = database

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self._database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    def validate_ownership(self) -> None:
        connection = self._connect()
        try:
            invalid_links = int(
                connection.execute(
                    "SELECT COUNT(*) FROM v4_assertion_links "
                    "WHERE relation_type NOT IN ('CONTRADICTS', 'SUPERSEDES')"
                ).fetchone()[0]
            )
            cross_scope_links = int(connection.execute("""
                    SELECT COUNT(*)
                    FROM v4_assertion_links links
                    JOIN v4_assertions source_assertion
                      ON source_assertion.assertion_id = links.source_assertion_id
                    JOIN memory_mutations source_mutation
                      ON source_mutation.mutation_id = source_assertion.mutation_id
                    JOIN v4_assertions target_assertion
                      ON target_assertion.assertion_id = links.target_assertion_id
                    JOIN memory_mutations target_mutation
                      ON target_mutation.mutation_id = target_assertion.mutation_id
                    WHERE source_assertion.tenant_id != target_assertion.tenant_id
                       OR source_mutation.agent_id != target_mutation.agent_id
                    """).fetchone()[0])
        finally:
            connection.close()
        if invalid_links or cross_scope_links:
            raise ProjectionOwnershipError("projection ownership validation failed")

    def provider_signatures(
        self,
    ) -> set[tuple[str | None, str | None, str | None, int | None]]:
        connection = self._connect()
        try:
            rows = connection.execute(f"""
                SELECT DISTINCT m.embedding_provider, m.embedding_model,
                                m.embedding_version, m.embedding_dimension
                FROM artifact_registry r
                JOIN artifact_sources s ON s.registry_id = r.registry_id
                                       AND s.state = 'ACTIVE'
                JOIN memory_mutations m ON m.mutation_id = s.mutation_id
                WHERE {_ACTIVE_OWNERSHIP}
                  AND (
                    (r.store_name = 'SQL' AND r.artifact_kind = 'ASSERTION')
                    OR (
                      r.store_name = 'VECTOR'
                      AND r.artifact_kind IN ('ASSERTION_VECTOR', 'ENTITY_VECTOR')
                    )
                  )
                """).fetchall()
        finally:
            connection.close()
        return {
            (
                str(row[0]) if row[0] is not None else None,
                str(row[1]) if row[1] is not None else None,
                str(row[2]) if row[2] is not None else None,
                int(row[3]) if row[3] is not None else None,
            )
            for row in rows
        }

    def counts(self) -> dict[str, int]:
        connection = self._connect()
        try:
            return {
                lane: int(
                    connection.execute(f"SELECT COUNT(*) FROM ({query})").fetchone()[0]
                )
                for lane, query in _QUERIES.items()
            }
        finally:
            connection.close()

    def fetch(self, lane: str, *, offset: int, limit: int) -> list[dict[str, Any]]:
        if lane not in _QUERIES or offset < 0 or not 1 <= limit <= 1000:
            raise ValueError("invalid projection replay page")
        connection = self._connect()
        try:
            rows = connection.execute(
                f"SELECT * FROM ({_QUERIES[lane]}) LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def vector_smoke_cases(self, *, limit: int) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("invalid retrieval smoke limit")
        connection = self._connect()
        try:
            rows = connection.execute(
                f"""
                SELECT DISTINCT r.tenant_id AS tenant_id, r.agent_id AS agent_id,
                                s.dataset_id AS dataset_id,
                                r.physical_artifact_id AS assertion_id,
                                {_VECTOR_PAYLOAD_TEXT_EXPR} AS payload_text
                FROM artifact_registry r
                JOIN artifact_sources s ON s.registry_id = r.registry_id
                                       AND s.state = 'ACTIVE'
                JOIN v4_assertions a ON a.assertion_id = r.physical_artifact_id
                                    AND a.tenant_id = r.tenant_id
                JOIN v4_entities subject ON subject.entity_id = a.subject_id
                LEFT JOIN v4_entities object_entity ON object_entity.entity_id = a.object_entity_id
                WHERE {_ACTIVE_OWNERSHIP}
                  AND r.store_name = 'VECTOR'
                  AND r.artifact_kind = 'ASSERTION_VECTOR'
                  AND s.dataset_id IS NOT NULL
                UNION
                SELECT DISTINCT r.tenant_id AS tenant_id, r.agent_id AS agent_id,
                                s.dataset_id AS dataset_id,
                                r.physical_artifact_id AS assertion_id,
                                entity.canonical_name AS payload_text
                FROM artifact_registry r
                JOIN artifact_sources s ON s.registry_id = r.registry_id
                                       AND s.state = 'ACTIVE'
                JOIN v4_entities entity ON entity.entity_id = r.physical_artifact_id
                                       AND entity.tenant_id = r.tenant_id
                WHERE {_ACTIVE_OWNERSHIP}
                  AND r.store_name = 'VECTOR'
                  AND r.artifact_kind = 'ENTITY_VECTOR'
                  AND s.dataset_id IS NOT NULL
                ORDER BY tenant_id, agent_id, dataset_id, assertion_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def allowed_vector_ids(
        self, *, tenant_id: str, agent_id: str, dataset_id: str
    ) -> set[str]:
        connection = self._connect()
        try:
            rows = connection.execute(
                f"""
                SELECT DISTINCT r.physical_artifact_id
                FROM artifact_registry r
                JOIN artifact_sources s ON s.registry_id = r.registry_id
                                       AND s.state = 'ACTIVE'
                WHERE {_ACTIVE_OWNERSHIP}
                  AND r.store_name = 'VECTOR'
                  AND r.artifact_kind IN ('ASSERTION_VECTOR', 'ENTITY_VECTOR')
                  AND r.tenant_id = ? AND r.agent_id = ? AND s.dataset_id = ?
                """,
                (tenant_id, agent_id, dataset_id),
            ).fetchall()
            return {str(row[0]) for row in rows}
        finally:
            connection.close()

    def retrieval_scopes(
        self, *, agent_id: str, limit: int = 100
    ) -> list[tuple[str, str]]:
        if not 1 <= limit <= 100:
            raise ValueError("invalid retrieval scope limit")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT DISTINCT r.tenant_id, s.dataset_id FROM artifact_sources s "
                "JOIN artifact_registry r ON r.registry_id = s.registry_id "
                "WHERE r.agent_id = ? "
                "AND r.state = 'ACTIVE' AND s.state = 'ACTIVE' "
                "AND r.store_name = 'VECTOR' "
                "AND r.artifact_kind IN ('ASSERTION_VECTOR', 'ENTITY_VECTOR') "
                "AND s.dataset_id IS NOT NULL "
                "ORDER BY r.tenant_id, s.dataset_id LIMIT ?",
                (agent_id, limit),
            ).fetchall()
            return [(str(row[0]), str(row[1])) for row in rows]
        finally:
            connection.close()


def _expected_provider_signature(
    provider_manifest: dict[str, Any],
) -> tuple[str, str, str, int]:
    provider = provider_manifest.get("embedding_provider")
    model = provider_manifest.get("embedding_model")
    version = provider_manifest.get("embedding_version")
    dimension = provider_manifest.get("dimension")
    if (
        not isinstance(provider, str)
        or not provider
        or not isinstance(model, str)
        or not model
        or not isinstance(version, str)
        or not version
        or isinstance(dimension, bool)
        or not isinstance(dimension, int)
        or dimension <= 0
    ):
        raise EmbeddingProviderConflictError(
            "embedding provider manifest is incomplete"
        )
    return provider, model, version, dimension


def _validate_provider(
    snapshot: ProjectionSnapshot, provider_manifest: dict[str, Any]
) -> tuple[str, str, str, int] | None:
    signatures = snapshot.provider_signatures()
    if not signatures:
        return None
    # A rebuild deliberately creates a *new* embedding generation from
    # canonical SQL.  Its target provider may therefore differ from the
    # admission-time provider recorded in the immutable source snapshot.
    # Source identities still have to be complete and internally coherent;
    # they are provenance, not an instruction to reuse the old space.
    if any(
        provider is None
        or model is None
        or version is None
        or dimension is None
        or dimension <= 0
        for provider, model, version, dimension in signatures
    ):
        raise EmbeddingProviderConflictError(
            "canonical vector source has incomplete embedding identity"
        )
    expected = _expected_provider_signature(provider_manifest)
    # Older rebuild callers did not carry a full target-space identity.  Keep
    # their historical same-provider safety contract while requiring new
    # migration callers to present the durable target identity explicitly.
    if "embedding_space_id" not in provider_manifest and (
        len(signatures) != 1 or next(iter(signatures)) != expected
    ):
        raise EmbeddingProviderConflictError("embedding provider manifest conflicts")
    return expected


class ProjectionReplayer:
    """Replay an immutable source snapshot into one empty staging generation."""

    def __init__(self, operations: OperationRepositoryPort) -> None:
        self._operations = operations

    async def replay(
        self,
        *,
        preparation: RebuildPreparation,
        trusted_root: Path,
        storage_root: Path,
        runner_id: str,
        provider_manifest: dict[str, Any],
        batch_size: int = 100,
        lease_seconds: int = 300,
        embedding_provider: EmbeddingProvider | None = None,
        embedding_service: Any | None = None,
        allow_model_loading: bool = False,
        local_embedding_model: str = "BAAI/bge-m3",
        vector_factory: Callable[[Path], VectorReplayTarget] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> RebuildReplayResult:
        if not 1 <= batch_size <= 1000:
            raise ValueError("rebuild batch size must be between 1 and 1000")
        current_manifest, current_hash = canonical_sqlite_manifest(
            preparation.backup_root / "mesa.db"
        )
        if (
            current_hash != preparation.source_manifest_hash
            or current_manifest["canonical_sha256"]
            != preparation.source_manifest["canonical_sha256"]
        ):
            raise RebuildSourceChangedError("rebuild source snapshot changed")
        snapshot = ProjectionSnapshot(preparation.backup_root / "mesa.db")
        snapshot.validate_ownership()
        expected_provider = _validate_provider(snapshot, provider_manifest)
        counts = snapshot.counts()
        total = sum(counts.values())

        operation = preparation.operation
        operation_id = str(operation["operation_id"])
        claim_token = str(operation["claim_token"])
        fencing_token = int(operation["fencing_token"])
        checkpoint = dict(operation.get("checkpoint") or {})
        replay_checkpoint = dict(checkpoint.get("replay") or {})
        offsets = {lane: int(replay_checkpoint.get(lane, 0)) for lane in _LANES}
        if any(offsets[lane] < 0 or offsets[lane] > counts[lane] for lane in _LANES):
            raise RebuildCheckpointError("rebuild checkpoint offset is invalid")
        completed = sum(offsets.values())
        if int(operation.get("progress_completed", completed)) != completed:
            raise RebuildCheckpointError("rebuild checkpoint progress conflicts")
        existing_total = int(operation.get("progress_total", 0))
        if existing_total not in {0, total}:
            raise RebuildCheckpointError("rebuild checkpoint total conflicts")

        paths = resolve_projection_generation_paths(
            preparation.generation,
            storage_root=storage_root,
            trusted_root=trusted_root,
            runtime_fencing_token=preparation.runtime_fencing_token,
        )
        paths.vector_path.parent.mkdir(parents=True, exist_ok=True)
        vector = (
            vector_factory(paths.vector_path)
            if vector_factory is not None
            else VectorEngine(
                str(paths.vector_path),
                allow_model_loading=allow_model_loading,
                embedding_provider=embedding_provider,
                embedding_service=embedding_service,
                local_embedding_model=local_embedding_model,
            )
        )
        try:
            if Path(vector.uri).resolve(strict=False) != paths.vector_path:
                raise RebuildReplayError(
                    "vector target does not match staging generation"
                )
            await vector.initialize()
            for lane in _LANES:
                while offsets[lane] < counts[lane]:
                    if should_stop is not None and should_stop():
                        raise RebuildInterruptedError(
                            "rebuild interrupted at a batch boundary"
                        )
                    operation = await self._operations.renew(
                        operation_id,
                        runner_id=runner_id,
                        claim_token=claim_token,
                        fencing_token=fencing_token,
                        lease_seconds=lease_seconds,
                    )
                    rows = snapshot.fetch(lane, offset=offsets[lane], limit=batch_size)
                    if not rows:
                        raise RebuildReplayError("projection source page is incomplete")
                    await self._apply_batch(
                        lane,
                        rows,
                        vector=vector,
                        expected_provider=expected_provider,
                    )
                    offsets[lane] += len(rows)
                    completed = sum(offsets.values())
                    checkpoint["phase"] = "REPLAYING"
                    checkpoint["replay"] = dict(offsets)
                    operation = await self._operations.transition(
                        operation_id,
                        to_state="RUNNING",
                        runner_id=runner_id,
                        claim_token=claim_token,
                        fencing_token=fencing_token,
                        progress_completed=completed,
                        progress_total=total,
                        checkpoint=checkpoint,
                    )
            if should_stop is not None and should_stop():
                raise RebuildInterruptedError(
                    "rebuild interrupted after replay checkpoint"
                )
            checkpoint["phase"] = "REPLAYED"
            checkpoint["staging_bytes"] = _staging_bytes(paths.vector_path)
            operation = await self._operations.transition(
                operation_id,
                to_state="VERIFYING",
                runner_id=runner_id,
                claim_token=claim_token,
                fencing_token=fencing_token,
                progress_completed=total,
                progress_total=total,
                checkpoint=checkpoint,
            )
        finally:
            await vector.close()
        return RebuildReplayResult(
            operation=operation,
            counts=counts,
            completed=total,
            total=total,
        )

    @staticmethod
    async def _apply_batch(
        lane: str,
        rows: list[dict[str, Any]],
        *,
        vector: VectorReplayTarget,
        expected_provider: tuple[str, str, str, int] | None,
    ) -> None:
        if lane == "vector":
            if expected_provider is None:
                raise EmbeddingProviderConflictError(
                    "vector source has no embedding provider identity"
                )
            embeddings = await vector.compute_embedding_batch(
                [str(row["payload_text"]) for row in rows]
            )
            dimension = expected_provider[3]
            if len(embeddings) != len(rows) or any(
                len(embedding) != dimension for embedding in embeddings
            ):
                raise EmbeddingProviderConflictError(
                    "embedding provider returned an incompatible dimension"
                )
            await vector.bulk_upsert(
                [
                    {
                        "node_id": str(row["assertion_id"]),
                        "agent_id": str(row["agent_id"]),
                        "embedding": embedding,
                        "content_hash": hashlib.sha256(
                            str(row["payload_text"]).encode()
                        ).hexdigest(),
                    }
                    for row, embedding in zip(rows, embeddings)
                ]
            )
        else:
            raise ValueError("unknown projection replay lane")
