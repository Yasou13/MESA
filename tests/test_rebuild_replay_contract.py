"""Deterministic vector and graph projection replay contracts."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config

from mesa_storage.rebuild_preparation import (
    RebuildPreparation,
    canonical_sqlite_manifest,
)
from mesa_storage.rebuild_replay import (
    EmbeddingProviderConflictError,
    ProjectionReplayer,
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
        vector = _VectorTarget(path)
        return vector

    def graph_factory(path: Path) -> _GraphTarget:
        nonlocal graph
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
