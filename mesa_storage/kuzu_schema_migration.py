"""Offline versioned migration of an existing Kùzu graph artifact."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import kuzu

from mesa_storage.kuzu_migration import KuzuMigrationCoordinator, MigrationOutcome
from mesa_storage.kuzu_setup import initialize_schema_artifact

CURRENT_SCHEMA_VERSION = "2"


@dataclass(frozen=True)
class GraphSnapshot:
    nodes: list[tuple[str, str, str, bool]]
    edges: list[tuple[str, str, float, str, str, float]]


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

    snapshot = _snapshot(live_path) if live_path.exists() else GraphSnapshot([], [])
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
        return GraphSnapshot(nodes=nodes, edges=edges)
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
    finally:
        connection.close()
        database.close()


def _digest_snapshot(snapshot: GraphSnapshot) -> str:
    digest = hashlib.sha256()
    for row in snapshot.nodes + snapshot.edges:
        digest.update(repr(row).encode())
        digest.update(b"\n")
    return digest.hexdigest()
