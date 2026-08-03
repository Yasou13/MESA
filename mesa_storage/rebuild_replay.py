"""Deterministic, resumable replay of vector and graph projections."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from mesa_storage.kuzu_provider import KuzuGraphProvider
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

_LANES = ("vector", "graph_entity", "graph_assertion", "graph_link")


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


class GraphReplayTarget(Protocol):
    @property
    def db_path(self) -> str: ...

    async def initialize(self) -> None: ...

    async def insert_node(self, node_id: str, name: str, agent_id: str) -> None: ...

    async def insert_assertion(
        self,
        *,
        assertion_id: str,
        subject_id: str,
        object_id: str | None,
        object_value: str | None = None,
        agent_id: str,
        predicate: str,
        mutation_id: str,
        source_ref: str = "",
        evidence_span: str = "",
        jurisdiction: str = "",
        authority_level: str = "",
        valid_from: str = "",
        valid_to: str = "",
        observed_at: str = "",
        confidence: float = 1.0,
        pipeline_run_id: str = "",
        status: str = "ACTIVE",
    ) -> None: ...

    async def link_assertions(
        self,
        *,
        source_assertion_id: str,
        target_assertion_id: str,
        agent_id: str,
        relation_type: str,
    ) -> None: ...

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
_VECTOR_QUERY = f"""
    SELECT DISTINCT r.agent_id, r.physical_artifact_id AS entity_id,
           e.canonical_name
    FROM artifact_registry r
    JOIN v4_entities e ON e.entity_id = r.physical_artifact_id
                      AND e.tenant_id = r.tenant_id
    WHERE {_ACTIVE_OWNERSHIP}
      AND r.store_name = 'VECTOR' AND r.artifact_kind = 'ENTITY_VECTOR'
    ORDER BY r.agent_id, r.physical_artifact_id
"""
_GRAPH_ENTITY_QUERY = f"""
    SELECT DISTINCT r.agent_id, r.physical_artifact_id AS entity_id,
           e.canonical_name
    FROM artifact_registry r
    JOIN v4_entities e ON e.entity_id = r.physical_artifact_id
                      AND e.tenant_id = r.tenant_id
    WHERE {_ACTIVE_OWNERSHIP}
      AND r.store_name = 'GRAPH' AND r.artifact_kind = 'ENTITY'
    ORDER BY r.agent_id, r.physical_artifact_id
"""
_GRAPH_ASSERTION_QUERY = f"""
    SELECT DISTINCT r.agent_id, a.assertion_id, a.subject_id,
           a.object_entity_id, a.literal_value, a.predicate, a.mutation_id,
           a.source_ref, a.evidence_span, a.jurisdiction, a.authority_level,
           a.valid_from, a.valid_to, a.observed_at, a.confidence,
           a.pipeline_run_id, a.status
    FROM artifact_registry r
    JOIN v4_assertions a ON a.assertion_id = r.physical_artifact_id
                        AND a.tenant_id = r.tenant_id
    JOIN memory_mutations m ON m.mutation_id = a.mutation_id
                           AND m.agent_id = r.agent_id
    WHERE {_ACTIVE_OWNERSHIP}
      AND r.store_name = 'GRAPH' AND r.artifact_kind = 'ASSERTION'
    ORDER BY r.agent_id, a.assertion_id
"""
_GRAPH_LINK_QUERY = f"""
    SELECT DISTINCT source_registry.agent_id, links.source_assertion_id,
           links.target_assertion_id, links.relation_type
    FROM v4_assertion_links links
    JOIN v4_assertions source_assertion
      ON source_assertion.assertion_id = links.source_assertion_id
    JOIN memory_mutations source_mutation
      ON source_mutation.mutation_id = source_assertion.mutation_id
    JOIN artifact_registry source_registry
      ON source_registry.physical_artifact_id = links.source_assertion_id
     AND source_registry.tenant_id = source_assertion.tenant_id
     AND source_registry.agent_id = source_mutation.agent_id
     AND source_registry.store_name = 'GRAPH'
     AND source_registry.artifact_kind = 'ASSERTION'
    JOIN v4_assertions target_assertion
      ON target_assertion.assertion_id = links.target_assertion_id
    JOIN memory_mutations target_mutation
      ON target_mutation.mutation_id = target_assertion.mutation_id
     AND target_mutation.agent_id = source_mutation.agent_id
    JOIN artifact_registry target_registry
      ON target_registry.physical_artifact_id = links.target_assertion_id
     AND target_registry.tenant_id = target_assertion.tenant_id
     AND target_registry.agent_id = source_registry.agent_id
     AND target_registry.store_name = 'GRAPH'
     AND target_registry.artifact_kind = 'ASSERTION'
    WHERE source_assertion.tenant_id = target_assertion.tenant_id
      AND {_ACTIVE_OWNERSHIP.replace("r.", "source_registry.")}
      AND {_ACTIVE_OWNERSHIP.replace("r.", "target_registry.")}
    ORDER BY source_registry.agent_id, links.source_assertion_id,
             links.target_assertion_id, links.relation_type
"""
_QUERIES = {
    "vector": _VECTOR_QUERY,
    "graph_entity": _GRAPH_ENTITY_QUERY,
    "graph_assertion": _GRAPH_ASSERTION_QUERY,
    "graph_link": _GRAPH_LINK_QUERY,
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
            missing_endpoints = int(connection.execute(f"""
                    SELECT COUNT(*) FROM ({_GRAPH_ASSERTION_QUERY}) assertion
                    WHERE NOT EXISTS (
                        SELECT 1 FROM ({_GRAPH_ENTITY_QUERY}) entity
                        WHERE entity.agent_id = assertion.agent_id
                          AND entity.entity_id = assertion.subject_id
                    ) OR (
                        assertion.object_entity_id IS NOT NULL AND NOT EXISTS (
                            SELECT 1 FROM ({_GRAPH_ENTITY_QUERY}) entity
                            WHERE entity.agent_id = assertion.agent_id
                              AND entity.entity_id = assertion.object_entity_id
                        )
                    )
                    """).fetchone()[0])
            invalid_links = int(
                connection.execute(
                    "SELECT COUNT(*) FROM v4_assertion_links "
                    "WHERE relation_type NOT IN ('CONTRADICTS', 'SUPERSEDES')"
                ).fetchone()[0]
            )
            cross_scope_links = int(connection.execute(f"""
                    SELECT COUNT(*)
                    FROM v4_assertion_links links
                    JOIN v4_assertions source_assertion
                      ON source_assertion.assertion_id = links.source_assertion_id
                    JOIN memory_mutations source_mutation
                      ON source_mutation.mutation_id = source_assertion.mutation_id
                    JOIN artifact_registry source_registry
                      ON source_registry.physical_artifact_id = links.source_assertion_id
                     AND source_registry.store_name = 'GRAPH'
                     AND source_registry.artifact_kind = 'ASSERTION'
                    JOIN v4_assertions target_assertion
                      ON target_assertion.assertion_id = links.target_assertion_id
                    JOIN memory_mutations target_mutation
                      ON target_mutation.mutation_id = target_assertion.mutation_id
                    JOIN artifact_registry target_registry
                      ON target_registry.physical_artifact_id = links.target_assertion_id
                     AND target_registry.store_name = 'GRAPH'
                     AND target_registry.artifact_kind = 'ASSERTION'
                    WHERE {_ACTIVE_OWNERSHIP.replace("r.", "source_registry.")}
                      AND {_ACTIVE_OWNERSHIP.replace("r.", "target_registry.")}
                      AND (
                        source_assertion.tenant_id != target_assertion.tenant_id
                        OR source_registry.agent_id != target_registry.agent_id
                        OR source_registry.agent_id != source_mutation.agent_id
                        OR target_registry.agent_id != target_mutation.agent_id
                      )
                    """).fetchone()[0])
        finally:
            connection.close()
        if missing_endpoints or invalid_links or cross_scope_links:
            raise ProjectionOwnershipError("projection ownership validation failed")

    def provider_signatures(self) -> set[tuple[str | None, str | None, int | None]]:
        connection = self._connect()
        try:
            rows = connection.execute(f"""
                SELECT DISTINCT m.embedding_model, m.embedding_version,
                                m.embedding_dimension
                FROM artifact_registry r
                JOIN artifact_sources s ON s.registry_id = r.registry_id
                                       AND s.state = 'ACTIVE'
                JOIN memory_mutations m ON m.mutation_id = s.mutation_id
                WHERE {_ACTIVE_OWNERSHIP}
                  AND r.store_name = 'VECTOR'
                  AND r.artifact_kind = 'ENTITY_VECTOR'
                """).fetchall()
        finally:
            connection.close()
        return {
            (
                str(row[0]) if row[0] is not None else None,
                str(row[1]) if row[1] is not None else None,
                int(row[2]) if row[2] is not None else None,
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
                SELECT DISTINCT r.tenant_id, r.agent_id, s.dataset_id,
                                r.physical_artifact_id AS entity_id,
                                e.canonical_name
                FROM artifact_registry r
                JOIN artifact_sources s ON s.registry_id = r.registry_id
                                       AND s.state = 'ACTIVE'
                JOIN v4_entities e ON e.entity_id = r.physical_artifact_id
                                  AND e.tenant_id = r.tenant_id
                WHERE {_ACTIVE_OWNERSHIP}
                  AND r.store_name = 'VECTOR'
                  AND r.artifact_kind = 'ENTITY_VECTOR'
                  AND s.dataset_id IS NOT NULL
                ORDER BY r.tenant_id, r.agent_id, s.dataset_id,
                         r.physical_artifact_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def allowed_vector_ids(self, *, agent_id: str, dataset_id: str) -> set[str]:
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
                  AND r.artifact_kind = 'ENTITY_VECTOR'
                  AND r.agent_id = ? AND s.dataset_id = ?
                """,
                (agent_id, dataset_id),
            ).fetchall()
            return {str(row[0]) for row in rows}
        finally:
            connection.close()

    def dataset_ids(self, *, tenant_id: str, agent_id: str) -> list[str]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT DISTINCT s.dataset_id FROM artifact_sources s "
                "JOIN artifact_registry r ON r.registry_id = s.registry_id "
                "WHERE r.tenant_id = ? AND r.agent_id = ? "
                "AND r.state = 'ACTIVE' AND s.state = 'ACTIVE' "
                "AND s.dataset_id IS NOT NULL ORDER BY s.dataset_id",
                (tenant_id, agent_id),
            ).fetchall()
            return [str(row[0]) for row in rows]
        finally:
            connection.close()


def _expected_provider_signature(
    provider_manifest: dict[str, Any],
) -> tuple[str, str, int]:
    model = provider_manifest.get("embedding_model")
    version = provider_manifest.get("embedding_version")
    dimension = provider_manifest.get("dimension")
    if (
        not isinstance(model, str)
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
    return model, version, dimension


def _validate_provider(
    snapshot: ProjectionSnapshot, provider_manifest: dict[str, Any]
) -> tuple[str, str, int] | None:
    signatures = snapshot.provider_signatures()
    if not signatures:
        return None
    expected = _expected_provider_signature(provider_manifest)
    if len(signatures) != 1 or next(iter(signatures)) != expected:
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
        allow_model_loading: bool = False,
        vector_factory: Callable[[Path], VectorReplayTarget] | None = None,
        graph_factory: Callable[[Path], GraphReplayTarget] | None = None,
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
            )
        )
        graph: GraphReplayTarget
        if graph_factory is None:
            from mesa_storage import kuzu_setup

            kuzu_setup.initialize_schema_artifact(str(paths.graph_path))
            graph = KuzuGraphProvider(str(paths.graph_path))
        else:
            graph = graph_factory(paths.graph_path)
        try:
            if Path(vector.uri).resolve(strict=False) != paths.vector_path:
                raise RebuildReplayError(
                    "vector target does not match staging generation"
                )
            if Path(graph.db_path).resolve(strict=False) != paths.graph_path:
                raise RebuildReplayError(
                    "graph target does not match staging generation"
                )
            await vector.initialize()
            await graph.initialize()
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
                        graph=graph,
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
            checkpoint["staging_bytes"] = _staging_bytes(
                paths.vector_path, paths.graph_path
            )
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
            await graph.close()
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
        graph: GraphReplayTarget,
        expected_provider: tuple[str, str, int] | None,
    ) -> None:
        if lane == "vector":
            if expected_provider is None:
                raise EmbeddingProviderConflictError(
                    "vector source has no embedding provider identity"
                )
            embeddings = await vector.compute_embedding_batch(
                [str(row["canonical_name"]) for row in rows]
            )
            dimension = expected_provider[2]
            if len(embeddings) != len(rows) or any(
                len(embedding) != dimension for embedding in embeddings
            ):
                raise EmbeddingProviderConflictError(
                    "embedding provider returned an incompatible dimension"
                )
            await vector.bulk_upsert(
                [
                    {
                        "node_id": str(row["entity_id"]),
                        "agent_id": str(row["agent_id"]),
                        "embedding": embedding,
                        "content_hash": hashlib.sha256(
                            str(row["canonical_name"]).encode()
                        ).hexdigest(),
                    }
                    for row, embedding in zip(rows, embeddings)
                ]
            )
        elif lane == "graph_entity":
            for row in rows:
                await graph.insert_node(
                    str(row["entity_id"]),
                    str(row["canonical_name"]),
                    str(row["agent_id"]),
                )
        elif lane == "graph_assertion":
            for row in rows:
                await graph.insert_assertion(
                    assertion_id=str(row["assertion_id"]),
                    subject_id=str(row["subject_id"]),
                    object_id=(
                        str(row["object_entity_id"])
                        if row["object_entity_id"] is not None
                        else None
                    ),
                    object_value=(
                        str(row["literal_value"])
                        if row["literal_value"] is not None
                        else None
                    ),
                    agent_id=str(row["agent_id"]),
                    predicate=str(row["predicate"]),
                    mutation_id=str(row["mutation_id"]),
                    source_ref=str(row["source_ref"]),
                    evidence_span=str(row["evidence_span"]),
                    jurisdiction=str(row["jurisdiction"]),
                    authority_level=str(row["authority_level"]),
                    valid_from=str(row["valid_from"]),
                    valid_to=str(row["valid_to"]),
                    observed_at=str(row["observed_at"]),
                    confidence=float(row["confidence"]),
                    pipeline_run_id=str(row["pipeline_run_id"]),
                    status=str(row["status"]),
                )
        elif lane == "graph_link":
            for row in rows:
                await graph.link_assertions(
                    source_assertion_id=str(row["source_assertion_id"]),
                    target_assertion_id=str(row["target_assertion_id"]),
                    agent_id=str(row["agent_id"]),
                    relation_type=str(row["relation_type"]),
                )
        else:
            raise ValueError("unknown projection replay lane")
