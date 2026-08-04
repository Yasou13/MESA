"""Deterministic vector and graph projection replay contracts."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config

from mesa_storage.projection_generations import (
    ProjectionPaths,
    resolve_projection_generation_paths,
)
from mesa_storage.rebuild_cutover import (
    ParityGatedActivator,
    ProjectionParityError,
    ProjectionParityReport,
    ProjectionParityVerifier,
    RebuildVerificationError,
)
from mesa_storage.rebuild_preparation import (
    RebuildPreparation,
    canonical_sqlite_manifest,
)
from mesa_storage.rebuild_replay import (
    EmbeddingProviderConflictError,
    ProjectionReplayer,
    ProjectionSnapshot,
    RebuildInterruptedError,
    RebuildReplayResult,
)

_OPERATION_ID = "11111111-2222-4333-8444-555555555555"


def _config(database: Path) -> Config:
    config = Config(str(Path(__file__).parents[1] / "mesa_storage" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database}")
    return config


def _source_database(tmp_path: Path) -> Path:
    database = tmp_path / "backup" / "mesa.db"
    database.parent.mkdir()
    command.upgrade(_config(database), "head")
    connection = sqlite3.connect(database)
    for entity_id, name in (("entity-1", "Alpha"), ("entity-2", "Beta")):
        connection.execute(
            "INSERT INTO v4_entities (entity_id, tenant_id, entity_type, "
            "canonical_name, normalized_name, identity_key) "
            "VALUES (?, 'tenant-a', 'concept', ?, lower(?), ?)",
            (entity_id, name, name, entity_id),
        )
    for index in (1, 2):
        connection.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, "
            "agent_id, state) VALUES (?, 'tenant-a', 'session-a', 'agent-a', "
            "'COMMITTED')",
            (f"pipeline-{index}",),
        )
        connection.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, "
            "agent_id, session_id, content_payload, pipeline_run_id, "
            "embedding_model, embedding_version, embedding_dimension, state) "
            "VALUES (?, ?, 'tenant-a', 'agent-a', 'session-a', 'source', ?, "
            "'embed-model', 'v1', 3, 'COMMITTED')",
            (f"mutation-{index}", f"candidate-{index}", f"pipeline-{index}"),
        )
        connection.execute(
            "INSERT INTO v4_assertions (assertion_id, tenant_id, dataset_id, "
            "subject_id, predicate, literal_value, source_ref, document_id, "
            "revision_id, chunk_id, evidence_span, confidence, status, "
            "mutation_id, pipeline_run_id) VALUES (?, 'tenant-a', 'dataset-a', "
            "?, 'RELATES_TO', ?, 'source-a', 'document-a', ?, ?, '0:4', 0.9, "
            "?, ?, ?)",
            (
                f"assertion-{index}",
                f"entity-{index}",
                f"literal-{index}",
                f"revision-{index}",
                f"chunk-{index}",
                "ACTIVE" if index == 2 else "SUPERSEDED",
                f"mutation-{index}",
                f"pipeline-{index}",
            ),
        )
    connection.execute(
        "INSERT INTO v4_assertion_links (source_assertion_id, "
        "target_assertion_id, relation_type, mutation_id) "
        "VALUES ('assertion-2', 'assertion-1', 'SUPERSEDES', 'mutation-2')"
    )

    artifacts = [
        ("vector-1", "VECTOR", "ENTITY_VECTOR", "entity-1", "mutation-1"),
        ("graph-e1", "GRAPH", "ENTITY", "entity-1", "mutation-1"),
        ("graph-e2", "GRAPH", "ENTITY", "entity-2", "mutation-2"),
        ("graph-a1", "GRAPH", "ASSERTION", "assertion-1", "mutation-1"),
        ("graph-a2", "GRAPH", "ASSERTION", "assertion-2", "mutation-2"),
    ]
    for registry_id, store, kind, artifact_id, mutation_id in artifacts:
        connection.execute(
            "INSERT INTO artifact_registry (registry_id, tenant_id, agent_id, "
            "dataset_id, store_name, artifact_kind, physical_artifact_id, state) "
            "VALUES (?, 'tenant-a', 'agent-a', 'dataset-a', ?, ?, ?, 'ACTIVE')",
            (registry_id, store, kind, artifact_id),
        )
        connection.execute(
            "INSERT INTO artifact_sources (source_ownership_id, registry_id, "
            "mutation_id, pipeline_run_id, dataset_id, source_ref, state) "
            "VALUES (?, ?, ?, ?, 'dataset-a', 'source-a', 'ACTIVE')",
            (
                f"source-{registry_id}",
                registry_id,
                mutation_id,
                "pipeline-1" if mutation_id == "mutation-1" else "pipeline-2",
            ),
        )
    connection.commit()
    connection.close()
    return database


def _add_tenant_vector(database: Path) -> None:
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO v4_entities (entity_id, tenant_id, entity_type, "
        "canonical_name, normalized_name, identity_key) "
        "VALUES ('entity-b', 'tenant-b', 'concept', 'Bravo', 'bravo', 'entity-b')"
    )
    connection.execute(
        "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, "
        "agent_id, state) VALUES ('pipeline-b', 'tenant-b', 'session-b', "
        "'agent-a', 'COMMITTED')"
    )
    connection.execute(
        "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, "
        "agent_id, session_id, content_payload, pipeline_run_id, "
        "embedding_model, embedding_version, embedding_dimension, state) "
        "VALUES ('mutation-b', 'candidate-b', 'tenant-b', 'agent-a', "
        "'session-b', 'source', 'pipeline-b', 'embed-model', 'v1', 3, 'COMMITTED')"
    )
    connection.execute(
        "INSERT INTO artifact_registry (registry_id, tenant_id, agent_id, "
        "dataset_id, store_name, artifact_kind, physical_artifact_id, state) "
        "VALUES ('vector-b', 'tenant-b', 'agent-a', 'dataset-a', 'VECTOR', "
        "'ENTITY_VECTOR', 'entity-b', 'ACTIVE')"
    )
    connection.execute(
        "INSERT INTO artifact_sources (source_ownership_id, registry_id, "
        "mutation_id, pipeline_run_id, dataset_id, source_ref, state) "
        "VALUES ('source-vector-b', 'vector-b', 'mutation-b', 'pipeline-b', "
        "'dataset-a', 'source-b', 'ACTIVE')"
    )
    connection.commit()
    connection.close()


class _VectorTarget:
    def __init__(self, path: Path) -> None:
        self.uri = str(path)
        self.initialized = False
        self.closed = False
        self.records: list[dict] = []

    async def initialize(self) -> None:
        self.initialized = True

    async def compute_embedding_batch(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), 0.5, 1.0] for text in texts]

    async def bulk_upsert(self, records: list[dict]) -> int:
        self.records.extend(records)
        return len(records)

    async def close(self) -> None:
        self.closed = True

    async def health_check(self) -> dict:
        return {"status": "healthy"}

    async def count_records(self, active_only: bool = True) -> dict[str, int]:
        return {"mesa_vectors_3": len(self.records)}

    async def get_existing_node_ids(
        self, agent_id: str, node_ids: list[str]
    ) -> set[str]:
        requested = set(node_ids)
        return {
            str(record["node_id"])
            for record in self.records
            if record["agent_id"] == agent_id and record["node_id"] in requested
        }

    async def search(
        self,
        _query_vector: list[float],
        *,
        limit: int = 10,
        agent_id: str | None = None,
        allowed_node_ids: set[str] | None = None,
        include_expired: bool = False,
    ) -> list[dict]:
        return [
            {"node_id": record["node_id"]}
            for record in self.records
            if agent_id is None or record["agent_id"] == agent_id
            if allowed_node_ids is None or record["node_id"] in allowed_node_ids
        ][:limit]


class _GraphTarget:
    def __init__(self, path: Path) -> None:
        self.db_path = str(path)
        self.initialized = False
        self.closed = False
        self.nodes: list[tuple[str, str, str]] = []
        self.assertions: list[dict] = []
        self.links: list[dict] = []

    async def initialize(self) -> None:
        self.initialized = True

    async def insert_node(self, node_id: str, name: str, agent_id: str) -> None:
        self.nodes.append((node_id, name, agent_id))

    async def insert_assertion(self, **kwargs: object) -> None:
        self.assertions.append(dict(kwargs))

    async def link_assertions(self, **kwargs: object) -> None:
        self.links.append(dict(kwargs))

    async def close(self) -> None:
        self.closed = True

    async def health_check(self) -> dict:
        return {"status": "healthy"}

    async def execute_query(
        self, query: str, parameters: dict | None = None
    ) -> list[tuple]:
        if "COUNT(n)" in query and "n:Entity" in query:
            return [(len(self.nodes),)]
        if "COUNT(n)" in query and "n:Assertion" in query:
            return [(len(self.assertions),)]
        if "COUNT(r)" in query:
            return [(len(self.links),)]
        identifiers = set((parameters or {}).get("ids", []))
        agent_id = (parameters or {}).get("agent_id")
        if "n:Entity" in query:
            return [
                (node_id,)
                for node_id, _name, agent in self.nodes
                if agent == agent_id and node_id in identifiers
            ]
        if "n:Assertion" in query:
            return [
                (item["assertion_id"],)
                for item in self.assertions
                if item["agent_id"] == agent_id and item["assertion_id"] in identifiers
            ]
        return []


def _preparation(tmp_path: Path, database: Path) -> RebuildPreparation:
    source_manifest, source_hash = canonical_sqlite_manifest(database)
    operation = {
        "operation_id": _OPERATION_ID,
        "state": "RUNNING",
        "claim_token": "claim-a",
        "fencing_token": 1,
        "progress_completed": 0,
        "progress_total": 0,
        "checkpoint": {"phase": "PREPARED"},
    }
    return RebuildPreparation(
        operation=operation,
        generation={
            "generation_id": f"rebuild-{_OPERATION_ID}",
            "vector_relative_path": (
                f"projection-generations/rebuild-{_OPERATION_ID}/vector.lance"
            ),
            "graph_relative_path": (
                f"projection-generations/rebuild-{_OPERATION_ID}/kuzu_db"
            ),
        },
        backup_root=database.parent,
        backup_manifest_hash="b" * 64,
        source_manifest=source_manifest,
        source_manifest_hash=source_hash,
        source_generation_id="legacy",
        target_generation_id=f"rebuild-{_OPERATION_ID}",
        runtime_fencing_token=0,
    )


@pytest.mark.asyncio
async def test_replay_rebuilds_vectors_assertions_provenance_status_and_links(
    tmp_path: Path,
) -> None:
    database = _source_database(tmp_path)
    trusted = tmp_path / "trusted"
    storage = trusted / "storage"
    storage.mkdir(parents=True)
    preparation = _preparation(tmp_path, database)
    operation = preparation.operation
    operations = SimpleNamespace(
        renew=AsyncMock(return_value=operation),
        transition=AsyncMock(return_value={**operation, "state": "VERIFYING"}),
    )
    vector: _VectorTarget | None = None
    graph: _GraphTarget | None = None

    def vector_factory(path: Path) -> _VectorTarget:
        nonlocal vector
        path.mkdir(parents=True)
        (path / "vectors.bin").write_bytes(b"vec")
        vector = _VectorTarget(path)
        return vector

    def graph_factory(path: Path) -> _GraphTarget:
        nonlocal graph
        path.mkdir(parents=True)
        (path / "graph.bin").write_bytes(b"graph")
        graph = _GraphTarget(path)
        return graph

    before = canonical_sqlite_manifest(database)[1]
    result = await ProjectionReplayer(operations).replay(  # type: ignore[arg-type]
        preparation=preparation,
        trusted_root=trusted,
        storage_root=storage,
        runner_id="runner-a",
        provider_manifest={
            "embedding_model": "embed-model",
            "embedding_version": "v1",
            "dimension": 3,
        },
        batch_size=1,
        vector_factory=vector_factory,
        graph_factory=graph_factory,
    )

    assert result.counts == {
        "vector": 1,
        "graph_entity": 2,
        "graph_assertion": 2,
        "graph_link": 1,
    }
    assert result.completed == result.total == 6
    assert vector is not None and vector.closed is True
    assert vector.records[0]["node_id"] == "entity-1"
    assert graph is not None and graph.closed is True
    assert [node[0] for node in graph.nodes] == ["entity-1", "entity-2"]
    assert [item["status"] for item in graph.assertions] == [
        "SUPERSEDED",
        "ACTIVE",
    ]
    assert graph.assertions[0]["source_ref"] == "source-a"
    assert graph.links == [
        {
            "source_assertion_id": "assertion-2",
            "target_assertion_id": "assertion-1",
            "agent_id": "agent-a",
            "relation_type": "SUPERSEDES",
        }
    ]
    assert canonical_sqlite_manifest(database)[1] == before
    assert operations.renew.await_count == 6
    assert operations.transition.await_count == 7
    assert operations.transition.await_args.kwargs["to_state"] == "VERIFYING"
    assert operations.transition.await_args.kwargs["checkpoint"]["staging_bytes"] == 8


@pytest.mark.asyncio
async def test_replay_fails_closed_before_opening_stores_on_provider_conflict(
    tmp_path: Path,
) -> None:
    database = _source_database(tmp_path)
    trusted = tmp_path / "trusted"
    storage = trusted / "storage"
    storage.mkdir(parents=True)
    preparation = _preparation(tmp_path, database)
    operations = SimpleNamespace(renew=AsyncMock(), transition=AsyncMock())
    vector_factory = AsyncMock()

    with pytest.raises(EmbeddingProviderConflictError, match="conflicts"):
        await ProjectionReplayer(operations).replay(  # type: ignore[arg-type]
            preparation=preparation,
            trusted_root=trusted,
            storage_root=storage,
            runner_id="runner-a",
            provider_manifest={
                "embedding_model": "different-model",
                "embedding_version": "v1",
                "dimension": 3,
            },
            vector_factory=vector_factory,
        )

    vector_factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_bounded_parity_checks_counts_ids_health_and_retrieval_smoke(
    tmp_path: Path,
) -> None:
    database = _source_database(tmp_path)
    trusted = tmp_path / "trusted"
    storage = trusted / "storage"
    storage.mkdir(parents=True)
    preparation = _preparation(tmp_path, database)
    paths = resolve_projection_generation_paths(
        preparation.generation,
        storage_root=storage,
        trusted_root=trusted,
        runtime_fencing_token=0,
    )
    vector = _VectorTarget(paths.vector_path)
    vector.records = [
        {
            "node_id": "entity-1",
            "agent_id": "agent-a",
            "embedding": [5.0, 0.5, 1.0],
        }
    ]
    graph = _GraphTarget(paths.graph_path)
    graph.nodes = [
        ("entity-1", "Alpha", "agent-a"),
        ("entity-2", "Beta", "agent-a"),
    ]
    graph.assertions = [
        {"assertion_id": "assertion-1", "agent_id": "agent-a"},
        {"assertion_id": "assertion-2", "agent_id": "agent-a"},
    ]
    graph.links = [{"relation_type": "SUPERSEDES"}]
    verifier = ProjectionParityVerifier()

    report = await verifier.verify(
        snapshot=ProjectionSnapshot(database),
        paths=paths,
        vector_factory=lambda _path: vector,
        graph_factory=lambda _path: graph,
        parity_limit=10,
        smoke_limit=10,
    )

    assert report.passed is True
    assert report.smoke_checked == 1
    vector.records.append(
        {
            "node_id": "orphan",
            "agent_id": "agent-a",
            "embedding": [1.0, 0.0, 0.0],
        }
    )
    with pytest.raises(ProjectionParityError) as failure:
        await verifier.verify(
            snapshot=ProjectionSnapshot(database),
            paths=paths,
            vector_factory=lambda _path: vector,
            graph_factory=lambda _path: graph,
        )
    assert failure.value.report.orphans == 1


@pytest.mark.asyncio
async def test_retrieval_smoke_rejects_provider_results_outside_tenant_dataset_scope(
    tmp_path: Path,
) -> None:
    database = _source_database(tmp_path)
    _add_tenant_vector(database)
    trusted = tmp_path / "trusted"
    storage = trusted / "storage"
    storage.mkdir(parents=True)
    preparation = _preparation(tmp_path, database)
    paths = resolve_projection_generation_paths(
        preparation.generation,
        storage_root=storage,
        trusted_root=trusted,
        runtime_fencing_token=0,
    )

    class LeakyVector(_VectorTarget):
        async def search(
            self,
            _query_vector: list[float],
            *,
            limit: int = 10,
            agent_id: str | None = None,
            allowed_node_ids: set[str] | None = None,
            include_expired: bool = False,
        ) -> list[dict]:
            del allowed_node_ids
            return await super().search(
                _query_vector,
                limit=limit,
                agent_id=agent_id,
                include_expired=include_expired,
            )

    vector = LeakyVector(paths.vector_path)
    vector.records = [
        {"node_id": "entity-1", "agent_id": "agent-a", "embedding": [1.0] * 3},
        {"node_id": "entity-b", "agent_id": "agent-a", "embedding": [0.0] * 3},
    ]
    graph = _GraphTarget(paths.graph_path)
    graph.nodes = [
        ("entity-1", "Alpha", "agent-a"),
        ("entity-2", "Beta", "agent-a"),
    ]
    graph.assertions = [
        {"assertion_id": "assertion-1", "agent_id": "agent-a"},
        {"assertion_id": "assertion-2", "agent_id": "agent-a"},
    ]
    graph.links = [{"relation_type": "SUPERSEDES"}]

    with pytest.raises(RebuildVerificationError, match="retrieval scope"):
        await ProjectionParityVerifier().verify(
            snapshot=ProjectionSnapshot(database),
            paths=paths,
            vector_factory=lambda _path: vector,
            graph_factory=lambda _path: graph,
        )


def test_snapshot_vector_scope_includes_tenant_predicate(tmp_path: Path) -> None:
    database = _source_database(tmp_path)
    _add_tenant_vector(database)
    snapshot = ProjectionSnapshot(database)

    assert snapshot.allowed_vector_ids(
        tenant_id="tenant-a", agent_id="agent-a", dataset_id="dataset-a"
    ) == {"entity-1"}
    assert snapshot.allowed_vector_ids(
        tenant_id="tenant-b", agent_id="agent-a", dataset_id="dataset-a"
    ) == {"entity-b"}
    assert snapshot.retrieval_scopes(agent_id="agent-a") == [
        ("tenant-a", "dataset-a"),
        ("tenant-b", "dataset-a"),
    ]


@pytest.mark.asyncio
async def test_parity_smoke_applies_live_dataset_scope_to_raw_vector_results(
    tmp_path: Path,
) -> None:
    database = _source_database(tmp_path)
    _add_second_vector(database, dataset_id="dataset-b")
    trusted = tmp_path / "trusted"
    storage = trusted / "storage"
    storage.mkdir(parents=True)
    preparation = _preparation(tmp_path, database)
    paths = resolve_projection_generation_paths(
        preparation.generation,
        storage_root=storage,
        trusted_root=trusted,
        runtime_fencing_token=0,
    )
    vector = _VectorTarget(paths.vector_path)
    vector.records = [
        {"node_id": "entity-1", "agent_id": "agent-a", "embedding": [1.0] * 3},
        {"node_id": "entity-2", "agent_id": "agent-a", "embedding": [2.0] * 3},
    ]
    graph = _GraphTarget(paths.graph_path)
    graph.nodes = [
        ("entity-1", "Alpha", "agent-a"),
        ("entity-2", "Beta", "agent-a"),
    ]
    graph.assertions = [
        {"assertion_id": "assertion-1", "agent_id": "agent-a"},
        {"assertion_id": "assertion-2", "agent_id": "agent-a"},
    ]
    graph.links = [{"relation_type": "SUPERSEDES"}]

    report = await ProjectionParityVerifier().verify(
        snapshot=ProjectionSnapshot(database),
        paths=paths,
        vector_factory=lambda _path: vector,
        graph_factory=lambda _path: graph,
        parity_limit=10,
        smoke_limit=10,
    )

    assert report.passed is True
    assert report.smoke_checked == 2
    assert report.cross_dataset_checked == 2


def _parity_report() -> ProjectionParityReport:
    return ProjectionParityReport(
        expected_vector=1,
        expected_graph_entities=2,
        expected_graph_assertions=2,
        expected_graph_links=1,
        actual_vector=1,
        actual_graph_entities=2,
        actual_graph_assertions=2,
        actual_graph_links=1,
        missing=0,
        orphans=0,
        smoke_checked=1,
        cross_dataset_checked=0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_generation_id", [None, "retained-older"])
async def test_parity_gated_activation_completes_and_retains_previous_generation(
    tmp_path: Path,
    previous_generation_id: str | None,
) -> None:
    database = _source_database(tmp_path)
    trusted = tmp_path / "trusted"
    storage = trusted / "storage"
    storage.mkdir(parents=True)
    preparation = _preparation(tmp_path, database)
    verifying_operation = {
        **preparation.operation,
        "state": "VERIFYING",
        "progress_completed": 6,
        "progress_total": 6,
    }
    ready_operation = {**verifying_operation, "state": "READY_TO_CUTOVER"}
    completed_operation = {**ready_operation, "state": "COMPLETED"}
    operations = SimpleNamespace(
        renew=AsyncMock(return_value=verifying_operation),
        transition=AsyncMock(side_effect=[ready_operation, completed_operation]),
    )
    generations = SimpleNamespace(
        activate=AsyncMock(
            return_value={
                "active_generation_id": preparation.target_generation_id,
                "previous_generation_id": "legacy",
                "fencing_token": 1,
            }
        ),
        rollback=AsyncMock(),
        resolve_active=AsyncMock(
            return_value=ProjectionPaths(
                generation_id="legacy",
                vector_path=storage / "vector.lance",
                graph_path=storage / "kuzu_db",
                runtime_fencing_token=0,
                previous_generation_id=previous_generation_id,
            )
        ),
    )
    verifier = SimpleNamespace(
        verify=AsyncMock(side_effect=[_parity_report(), _parity_report()])
    )
    replay = RebuildReplayResult(
        operation=verifying_operation,
        counts={
            "vector": 1,
            "graph_entity": 2,
            "graph_assertion": 2,
            "graph_link": 1,
        },
        completed=6,
        total=6,
    )

    result = await ParityGatedActivator(
        operations,
        generations,
        verifier,  # type: ignore[arg-type]
    ).activate(
        preparation=preparation,
        replay=replay,
        trusted_root=trusted,
        storage_root=storage,
        runner_id="runner-a",
        vector_factory=lambda _path: None,  # type: ignore[return-value]
        graph_factory=lambda _path: None,  # type: ignore[return-value]
    )

    assert result.operation["state"] == "COMPLETED"
    assert result.active_generation_id == preparation.target_generation_id
    assert result.retained_generation_id == "legacy"
    generations.activate.assert_awaited_once()
    generations.rollback.assert_not_awaited()
    assert operations.transition.await_args.kwargs["to_state"] == "COMPLETED"


@pytest.mark.asyncio
async def test_post_cutover_failure_rolls_pointer_back_and_keeps_retained_generation(
    tmp_path: Path,
) -> None:
    database = _source_database(tmp_path)
    trusted = tmp_path / "trusted"
    storage = trusted / "storage"
    storage.mkdir(parents=True)
    preparation = _preparation(tmp_path, database)
    verifying_operation = {
        **preparation.operation,
        "state": "VERIFYING",
        "progress_completed": 6,
        "progress_total": 6,
    }
    ready_operation = {**verifying_operation, "state": "READY_TO_CUTOVER"}
    operations = SimpleNamespace(
        renew=AsyncMock(return_value=verifying_operation),
        transition=AsyncMock(
            side_effect=[
                ready_operation,
                {**ready_operation, "state": "RETRYABLE_FAILED"},
            ]
        ),
    )
    retained_paths = ProjectionPaths(
        generation_id="legacy",
        vector_path=storage / "vector.lance",
        graph_path=storage / "kuzu_db",
        runtime_fencing_token=2,
        previous_generation_id=None,
    )
    generations = SimpleNamespace(
        activate=AsyncMock(
            return_value={
                "active_generation_id": preparation.target_generation_id,
                "previous_generation_id": "legacy",
                "fencing_token": 1,
            }
        ),
        rollback=AsyncMock(
            return_value={"active_generation_id": "legacy", "fencing_token": 2}
        ),
        resolve_active=AsyncMock(
            side_effect=[
                ProjectionPaths(
                    generation_id="legacy",
                    vector_path=storage / "vector.lance",
                    graph_path=storage / "kuzu_db",
                    runtime_fencing_token=0,
                    previous_generation_id=None,
                ),
                retained_paths,
            ]
        ),
    )
    verifier = SimpleNamespace(
        verify=AsyncMock(
            side_effect=[
                _parity_report(),
                RebuildVerificationError("post-cutover probe failed"),
                _parity_report(),
            ]
        )
    )
    replay = RebuildReplayResult(
        operation=verifying_operation,
        counts={
            "vector": 1,
            "graph_entity": 2,
            "graph_assertion": 2,
            "graph_link": 1,
        },
        completed=6,
        total=6,
    )

    with pytest.raises(RebuildVerificationError, match="rolled back"):
        await ParityGatedActivator(
            operations,
            generations,
            verifier,  # type: ignore[arg-type]
        ).activate(
            preparation=preparation,
            replay=replay,
            trusted_root=trusted,
            storage_root=storage,
            runner_id="runner-a",
            vector_factory=lambda _path: None,  # type: ignore[return-value]
            graph_factory=lambda _path: None,  # type: ignore[return-value]
        )

    generations.activate.assert_awaited_once()
    generations.rollback.assert_awaited_once()
    assert generations.resolve_active.await_count == 2
    assert operations.transition.await_args.kwargs["error_class"] == (
        "PostCutoverVerificationFailed"
    )
    assert operations.transition.await_args.kwargs["checkpoint"]["rollback_count"] == 1


@pytest.mark.asyncio
async def test_activation_crash_recovery_verifies_existing_pointer_without_reactivation(
    tmp_path: Path,
) -> None:
    database = _source_database(tmp_path)
    trusted = tmp_path / "trusted"
    storage = trusted / "storage"
    storage.mkdir(parents=True)
    original = _preparation(tmp_path, database)
    ready_operation = {
        **original.operation,
        "state": "READY_TO_CUTOVER",
        "progress_completed": 6,
        "progress_total": 6,
        "checkpoint": {"phase": "READY_TO_CUTOVER"},
    }
    preparation = replace(original, operation=ready_operation)
    completed_operation = {**ready_operation, "state": "COMPLETED"}
    operations = SimpleNamespace(
        renew=AsyncMock(return_value=ready_operation),
        transition=AsyncMock(side_effect=[ready_operation, completed_operation]),
    )
    generations = SimpleNamespace(
        resolve_active=AsyncMock(
            return_value=ProjectionPaths(
                generation_id=preparation.target_generation_id,
                vector_path=(
                    storage
                    / "projection-generations"
                    / preparation.target_generation_id
                    / "vector.lance"
                ),
                graph_path=(
                    storage
                    / "projection-generations"
                    / preparation.target_generation_id
                    / "kuzu_db"
                ),
                runtime_fencing_token=1,
                previous_generation_id="legacy",
            )
        ),
        activate=AsyncMock(),
        rollback=AsyncMock(),
    )
    verifier = SimpleNamespace(
        verify=AsyncMock(side_effect=[_parity_report(), _parity_report()])
    )
    replay = RebuildReplayResult(
        operation=ready_operation,
        counts={
            "vector": 1,
            "graph_entity": 2,
            "graph_assertion": 2,
            "graph_link": 1,
        },
        completed=6,
        total=6,
    )

    result = await ParityGatedActivator(
        operations,
        generations,
        verifier,  # type: ignore[arg-type]
    ).activate(
        preparation=preparation,
        replay=replay,
        trusted_root=trusted,
        storage_root=storage,
        runner_id="runner-a",
        vector_factory=lambda _path: None,  # type: ignore[return-value]
        graph_factory=lambda _path: None,  # type: ignore[return-value]
    )

    assert result.operation["state"] == "COMPLETED"
    generations.activate.assert_not_awaited()
    generations.rollback.assert_not_awaited()
    assert verifier.verify.await_count == 2


class _RecordingOperations:
    def __init__(self, operation: dict) -> None:
        self.current = dict(operation)
        self.transitions: list[dict] = []

    async def renew(self, *_args: object, **_kwargs: object) -> dict:
        return dict(self.current)

    async def transition(self, *_args: object, **kwargs: object) -> dict:
        self.current = {
            **self.current,
            "state": kwargs["to_state"],
            "progress_completed": kwargs.get(
                "progress_completed", self.current["progress_completed"]
            ),
            "progress_total": kwargs.get(
                "progress_total", self.current["progress_total"]
            ),
            "checkpoint": dict(kwargs.get("checkpoint") or {}),
        }
        self.transitions.append(dict(kwargs))
        return dict(self.current)


def _add_second_vector(database: Path, *, dataset_id: str = "dataset-a") -> None:
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO artifact_registry (registry_id, tenant_id, agent_id, "
        "dataset_id, store_name, artifact_kind, physical_artifact_id, state) "
        "VALUES ('vector-2', 'tenant-a', 'agent-a', ?, 'VECTOR', "
        "'ENTITY_VECTOR', 'entity-2', 'ACTIVE')",
        (dataset_id,),
    )
    connection.execute(
        "INSERT INTO artifact_sources (source_ownership_id, registry_id, "
        "mutation_id, pipeline_run_id, dataset_id, source_ref, state) "
        "VALUES ('source-vector-2', 'vector-2', 'mutation-2', 'pipeline-2', ?, "
        "'source-a', 'ACTIVE')",
        (dataset_id,),
    )
    connection.commit()
    connection.close()


@pytest.mark.asyncio
async def test_vector_crash_keeps_only_the_last_completed_batch_checkpoint(
    tmp_path: Path,
) -> None:
    database = _source_database(tmp_path)
    _add_second_vector(database)
    trusted = tmp_path / "trusted"
    storage = trusted / "storage"
    storage.mkdir(parents=True)
    preparation = _preparation(tmp_path, database)
    operations = _RecordingOperations(preparation.operation)

    class CrashingVector(_VectorTarget):
        async def bulk_upsert(self, records: list[dict]) -> int:
            if self.records:
                raise RuntimeError("injected vector crash")
            return await super().bulk_upsert(records)

    vector: CrashingVector | None = None
    graph: _GraphTarget | None = None

    def vector_factory(path: Path) -> CrashingVector:
        nonlocal vector
        vector = CrashingVector(path)
        return vector

    def graph_factory(path: Path) -> _GraphTarget:
        nonlocal graph
        graph = _GraphTarget(path)
        return graph

    with pytest.raises(RuntimeError, match="vector crash"):
        await ProjectionReplayer(operations).replay(  # type: ignore[arg-type]
            preparation=preparation,
            trusted_root=trusted,
            storage_root=storage,
            runner_id="runner-a",
            provider_manifest={
                "embedding_model": "embed-model",
                "embedding_version": "v1",
                "dimension": 3,
            },
            batch_size=1,
            vector_factory=vector_factory,
            graph_factory=graph_factory,
        )

    assert operations.current["progress_completed"] == 1
    assert operations.current["checkpoint"]["replay"] == {
        "vector": 1,
        "graph_entity": 0,
        "graph_assertion": 0,
        "graph_link": 0,
    }
    assert vector is not None and vector.closed is True
    assert graph is not None and graph.closed is True


@pytest.mark.asyncio
async def test_graph_crash_does_not_advance_the_failed_graph_batch(
    tmp_path: Path,
) -> None:
    database = _source_database(tmp_path)
    trusted = tmp_path / "trusted"
    storage = trusted / "storage"
    storage.mkdir(parents=True)
    preparation = _preparation(tmp_path, database)
    operations = _RecordingOperations(preparation.operation)
    paths = resolve_projection_generation_paths(
        preparation.generation,
        storage_root=storage,
        trusted_root=trusted,
        runtime_fencing_token=0,
    )

    class CrashingGraph(_GraphTarget):
        async def insert_node(self, node_id: str, name: str, agent_id: str) -> None:
            if self.nodes:
                raise RuntimeError("injected graph crash")
            await super().insert_node(node_id, name, agent_id)

    vector = _VectorTarget(paths.vector_path)
    graph = CrashingGraph(paths.graph_path)
    with pytest.raises(RuntimeError, match="graph crash"):
        await ProjectionReplayer(operations).replay(  # type: ignore[arg-type]
            preparation=preparation,
            trusted_root=trusted,
            storage_root=storage,
            runner_id="runner-a",
            provider_manifest={
                "embedding_model": "embed-model",
                "embedding_version": "v1",
                "dimension": 3,
            },
            batch_size=1,
            vector_factory=lambda _path: vector,
            graph_factory=lambda _path: graph,
        )

    assert operations.current["progress_completed"] == 2
    assert operations.current["checkpoint"]["replay"] == {
        "vector": 1,
        "graph_entity": 1,
        "graph_assertion": 0,
        "graph_link": 0,
    }
    assert vector.closed is True
    assert graph.closed is True


@pytest.mark.asyncio
async def test_interruption_resumes_after_the_durable_batch_without_replaying_it(
    tmp_path: Path,
) -> None:
    database = _source_database(tmp_path)
    trusted = tmp_path / "trusted"
    storage = trusted / "storage"
    storage.mkdir(parents=True)
    preparation = _preparation(tmp_path, database)
    operations = _RecordingOperations(preparation.operation)
    paths = resolve_projection_generation_paths(
        preparation.generation,
        storage_root=storage,
        trusted_root=trusted,
        runtime_fencing_token=0,
    )
    vector = _VectorTarget(paths.vector_path)
    graph = _GraphTarget(paths.graph_path)

    with pytest.raises(RebuildInterruptedError, match="batch boundary"):
        await ProjectionReplayer(operations).replay(  # type: ignore[arg-type]
            preparation=preparation,
            trusted_root=trusted,
            storage_root=storage,
            runner_id="runner-a",
            provider_manifest={
                "embedding_model": "embed-model",
                "embedding_version": "v1",
                "dimension": 3,
            },
            batch_size=1,
            vector_factory=lambda _path: vector,
            graph_factory=lambda _path: graph,
            should_stop=lambda: operations.current["progress_completed"] == 1,
        )

    assert [record["node_id"] for record in vector.records] == ["entity-1"]
    resumed = replace(preparation, operation=dict(operations.current))
    result = await ProjectionReplayer(operations).replay(  # type: ignore[arg-type]
        preparation=resumed,
        trusted_root=trusted,
        storage_root=storage,
        runner_id="runner-a",
        provider_manifest={
            "embedding_model": "embed-model",
            "embedding_version": "v1",
            "dimension": 3,
        },
        batch_size=1,
        vector_factory=lambda _path: vector,
        graph_factory=lambda _path: graph,
        should_stop=lambda: False,
    )

    assert result.completed == result.total == 6
    assert [record["node_id"] for record in vector.records] == ["entity-1"]
    assert [node[0] for node in graph.nodes] == ["entity-1", "entity-2"]
    assert operations.current["state"] == "VERIFYING"
