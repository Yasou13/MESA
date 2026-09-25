"""Offline versioned migration of an existing Kùzu graph artifact."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import kuzu

from mesa_storage.kuzu_migration import KuzuMigrationCoordinator, MigrationOutcome
from mesa_storage.kuzu_setup import initialize_schema_artifact

CURRENT_SCHEMA_VERSION = "4"


@dataclass(frozen=True)
class GraphSnapshot:
    nodes: list[tuple[str, str, str, bool]]
    edges: list[tuple[str, str, float, str, str, float]]
    assertions: list[tuple[Any, ...]]
    assertion_subjects: list[tuple[str, str]]
    assertion_objects: list[tuple[str, str]]
    assertion_links: list[tuple[str, str, str]]


def _migration_id(live_path: Path) -> str:
    path_hash = hashlib.sha256(str(live_path).encode()).hexdigest()[:16]
    return f"kuzu-schema-v{CURRENT_SCHEMA_VERSION}-{path_hash}"


def _journal_path(live_path: Path) -> Path:
    return live_path.parent / "kuzu_migration_journal.db"


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_schema_ready(live_path: Path) -> None:
    """Allow fresh graphs; reject existing artifacts without offline proof."""
    live_path = live_path.resolve()
    coordinator = KuzuMigrationCoordinator(live_path, _journal_path(live_path))
    migration_id = _migration_id(live_path)
    if coordinator.is_promoted(migration_id, CURRENT_SCHEMA_VERSION):
        return
    if live_path.exists():
        raise RuntimeError(
            "Kùzu schema is not version-journaled. Stop all application processes "
            "and run `python scripts/migrate_kuzu_schema.py --kuzu-db "
            f"{live_path}` before startup."
        )
    migrate_schema_offline(live_path)


def migrate_schema_offline(live_path: Path) -> MigrationOutcome:
    """Copy an existing graph to the current schema and atomically promote it."""
    live_path = live_path.resolve()
    coordinator = KuzuMigrationCoordinator(live_path, _journal_path(live_path))
    migration_id = _migration_id(live_path)
    if coordinator.is_promoted(migration_id, CURRENT_SCHEMA_VERSION):
        return coordinator.promoted_outcome(migration_id, CURRENT_SCHEMA_VERSION)

    snapshot = (
        _snapshot(live_path)
        if live_path.exists()
        else GraphSnapshot([], [], [], [], [], [])
    )
    source_fingerprint = _fingerprint(live_path) if live_path.exists() else "fresh"

    def build(staging_path: Path) -> None:
        initialize_schema_artifact(str(staging_path))
        _load_snapshot(staging_path, snapshot)

    def validate(staging_path: Path) -> None:
        observed = _snapshot(staging_path)
        if _digest_snapshot(observed) != _digest_snapshot(snapshot):
            raise RuntimeError("staging graph checksum differs from source graph")

    return coordinator.run(
        migration_id=migration_id,
        source_fingerprint=source_fingerprint,
        target_version=CURRENT_SCHEMA_VERSION,
        build_staging=build,
        validate_staging=validate,
    )


def _snapshot(path: Path) -> GraphSnapshot:
    database = kuzu.Database(str(path))
    connection = kuzu.Connection(database)
    try:
        try:
            nodes = [
                (str(row[0]), str(row[1]), str(row[2]), bool(row[3]))
                for row in _rows(
                    connection,
                    "MATCH (n:Entity) RETURN n.id, n.name, n.agent_id, n.is_quarantined "
                    "ORDER BY n.id",
                )
            ]
        except RuntimeError:
            nodes = [
                (str(row[0]), str(row[1]), str(row[2]), False)
                for row in _rows(
                    connection,
                    "MATCH (n:Entity) RETURN n.id, n.name, n.agent_id ORDER BY n.id",
                )
            ]
        try:
            edges = [
                (
                    str(row[0]),
                    str(row[1]),
                    float(row[2]),
                    str(row[3]),
                    str(row[4]),
                    float(row[5]),
                )
                for row in _rows(
                    connection,
                    "MATCH (a:Entity)-[r:Observed]->(b:Entity) "
                    "RETURN a.id, b.id, r.weight, r.updated_at, r.agent_id, "
                    "r.epistemic_uncertainty ORDER BY a.id, b.id",
                )
            ]
        except RuntimeError:
            edges = [
                (str(row[0]), str(row[1]), float(row[2]), str(row[3]), str(row[4]), 0.0)
                for row in _rows(
                    connection,
                    "MATCH (a:Entity)-[r:Observed]->(b:Entity) "
                    "RETURN a.id, b.id, r.weight, r.updated_at, r.agent_id ORDER BY a.id, b.id",
                )
            ]
        try:
            assertion_subjects = [
                (str(row[0]), str(row[1]))
                for row in _rows(
                    connection,
                    "MATCH (a:Assertion)-[:AssertionSubject]->(e:Entity) "
                    "RETURN a.id, e.id ORDER BY a.id, e.id",
                )
            ]
            assertion_objects = [
                (str(row[0]), str(row[1]))
                for row in _rows(
                    connection,
                    "MATCH (a:Assertion)-[:AssertionObject]->(e:Entity) "
                    "RETURN a.id, e.id ORDER BY a.id, e.id",
                )
            ]
            assertion_links = [
                (str(row[0]), str(row[1]), str(row[2]))
                for row in _rows(
                    connection,
                    "MATCH (a:Assertion)-[r:AssertionLink]->(b:Assertion) "
                    "RETURN a.id, b.id, r.relation_type ORDER BY a.id, b.id",
                )
            ]
            try:
                assertions = [
                    tuple(row)
                    for row in _rows(
                        connection,
                        "MATCH (a:Assertion) RETURN a.id, a.agent_id, a.predicate, "
                        "a.object_value, a.source_ref, a.evidence_span, "
                        "a.jurisdiction, a.authority_level, a.valid_from, a.valid_to, "
                        "a.observed_at, a.confidence, a.status, a.mutation_id, "
                        "a.pipeline_run_id, a.object_type, a.representation_version "
                        "ORDER BY a.id",
                    )
                ]
            except RuntimeError:
                entity_object_assertions = {
                    assertion_id for assertion_id, _ in assertion_objects
                }
                assertions = []
                for row in _rows(
                    connection,
                    "MATCH (a:Assertion) RETURN a.id, a.agent_id, a.predicate, "
                    "a.object_value, a.source_ref, a.evidence_span, "
                    "a.jurisdiction, a.authority_level, a.valid_from, a.valid_to, "
                    "a.observed_at, a.confidence, a.status, a.mutation_id, "
                    "a.pipeline_run_id ORDER BY a.id",
                ):
                    assertion_id = str(row[0])
                    object_type = (
                        "ENTITY"
                        if assertion_id in entity_object_assertions
                        else "LEGACY_LITERAL"
                    )
                    assertions.append((*tuple(row), object_type, "legacy-v0"))
        except RuntimeError:
            assertions = []
            assertion_subjects = []
            assertion_objects = []
            assertion_links = []
        return GraphSnapshot(
            nodes=nodes,
            edges=edges,
            assertions=assertions,
            assertion_subjects=assertion_subjects,
            assertion_objects=assertion_objects,
            assertion_links=assertion_links,
        )
    finally:
        connection.close()
        database.close()


def _rows(connection: kuzu.Connection, query: str) -> list[tuple[Any, ...]]:
    result = connection.execute(query)
    rows: list[tuple[Any, ...]] = []
    results = result if isinstance(result, list) else [result]
    for query_result in results:
        while query_result.has_next():
            value = query_result.get_next()
            rows.append(
                tuple(value.values()) if isinstance(value, dict) else tuple(value)
            )
    return rows


def _load_snapshot(path: Path, snapshot: GraphSnapshot) -> None:
    database = kuzu.Database(str(path))
    connection = kuzu.Connection(database)
    try:
        for node_id, name, agent_id, quarantined in snapshot.nodes:
            connection.execute(
                "CREATE (:Entity {id: $id, name: $name, agent_id: $agent_id, "
                "is_quarantined: $quarantined})",
                {
                    "id": node_id,
                    "name": name,
                    "agent_id": agent_id,
                    "quarantined": quarantined,
                },
            )
        for (
            source_id,
            target_id,
            weight,
            updated_at,
            agent_id,
            uncertainty,
        ) in snapshot.edges:
            connection.execute(
                "MATCH (a:Entity {id: $source}), (b:Entity {id: $target}) "
                "CREATE (a)-[:Observed {weight: $weight, updated_at: $updated_at, "
                "agent_id: $agent_id, epistemic_uncertainty: $uncertainty}]->(b)",
                {
                    "source": source_id,
                    "target": target_id,
                    "weight": weight,
                    "updated_at": updated_at,
                    "agent_id": agent_id,
                    "uncertainty": uncertainty,
                },
            )
        assertion_fields = (
            "id",
            "agent_id",
            "predicate",
            "object_value",
            "source_ref",
            "evidence_span",
            "jurisdiction",
            "authority_level",
            "valid_from",
            "valid_to",
            "observed_at",
            "confidence",
            "status",
            "mutation_id",
            "pipeline_run_id",
            "object_type",
            "representation_version",
        )
        for assertion_row in snapshot.assertions:
            params = dict(zip(assertion_fields, assertion_row))
            connection.execute(
                "CREATE (:Assertion {id: $id, agent_id: $agent_id, "
                "predicate: $predicate, object_value: $object_value, "
                "source_ref: $source_ref, evidence_span: $evidence_span, "
                "jurisdiction: $jurisdiction, authority_level: $authority_level, "
                "valid_from: $valid_from, valid_to: $valid_to, "
                "observed_at: $observed_at, confidence: $confidence, status: $status, "
                "mutation_id: $mutation_id, pipeline_run_id: $pipeline_run_id, "
                "object_type: $object_type, "
                "representation_version: $representation_version})",
                params,
            )
        for assertion_id, entity_id in snapshot.assertion_subjects:
            connection.execute(
                "MATCH (a:Assertion {id: $assertion}), (e:Entity {id: $entity}) "
                "CREATE (a)-[:AssertionSubject]->(e)",
                {"assertion": assertion_id, "entity": entity_id},
            )
        for assertion_id, entity_id in snapshot.assertion_objects:
            connection.execute(
                "MATCH (a:Assertion {id: $assertion}), (e:Entity {id: $entity}) "
                "CREATE (a)-[:AssertionObject]->(e)",
                {"assertion": assertion_id, "entity": entity_id},
            )
        for source_id, target_id, relation_type in snapshot.assertion_links:
            connection.execute(
                "MATCH (a:Assertion {id: $source}), (b:Assertion {id: $target}) "
                "CREATE (a)-[:AssertionLink {relation_type: $relation_type}]->(b)",
                {
                    "source": source_id,
                    "target": target_id,
                    "relation_type": relation_type,
                },
            )
    finally:
        connection.close()
        database.close()


def _digest_snapshot(snapshot: GraphSnapshot) -> str:
    digest = hashlib.sha256()
    for row in (
        snapshot.nodes
        + snapshot.edges
        + snapshot.assertions
        + snapshot.assertion_subjects
        + snapshot.assertion_objects
        + snapshot.assertion_links
    ):
        digest.update(repr(row).encode())
        digest.update(b"\n")
    return digest.hexdigest()
