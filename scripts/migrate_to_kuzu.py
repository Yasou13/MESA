#!/usr/bin/env python3
"""
MESA Data Migration Script — SQLite edges → KùzuDB bulk import.

Migrates legacy production graph data (nodes + edges) from the SQLite
relational layer into the KùzuDB graph engine using native bulk COPY,
which is orders of magnitude faster than row-by-row INSERT/MERGE.

Column mapping:
    SQLite nodes  → KùzuDB Entity   : id, entity_name→name, agent_id
    SQLite edges  → KùzuDB Observed : source_id→FROM, target_id→TO,
                                       weight, created_at→updated_at, agent_id

Usage:
    python scripts/migrate_to_kuzu.py \\
        --sqlite-db ./storage/mesa.db \\
        --kuzu-db   ./storage/kuzu_db \\
        [--csv-dir  ./storage/migration_csv]

The script uses ``KuzuMigrationCoordinator``. It builds a versioned sibling
staging database, validates counts, and atomically promotes it only after a
SQLite journal and process-level lock agree. The prior live directory is kept
for explicit rollback. ``--wipe`` is refused: live Kùzu data is never mutated
in place.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

import kuzu

from mesa_storage.kuzu_migration import KuzuMigrationCoordinator

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("MESA_Migration")

# ---------------------------------------------------------------------------
# KùzuDB DDL (mirrors mesa_storage/kuzu_setup.py)
# ---------------------------------------------------------------------------

_DDL_ENTITY = (
    "CREATE NODE TABLE IF NOT EXISTS Entity ("
    "id STRING, "
    "name STRING, "
    "agent_id STRING, "
    "PRIMARY KEY (id)"
    ")"
)

_DDL_OBSERVED = (
    "CREATE REL TABLE IF NOT EXISTS Observed ("
    "FROM Entity TO Entity, "
    "weight DOUBLE, "
    "updated_at TIMESTAMP, "
    "agent_id STRING"
    ")"
)

# ---------------------------------------------------------------------------
# Phase 1: Extract from SQLite → CSV
# ---------------------------------------------------------------------------


def extract_nodes(sqlite_path: str, csv_path: str) -> int:
    """Export active nodes from SQLite to a CSV file for KùzuDB COPY.

    CSV columns: id, name, agent_id
    Only rows where ``invalid_at IS NULL AND deleted_at IS NULL``
    are exported — soft-deleted nodes are excluded.

    Returns:
        Number of rows exported.
    """
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            "SELECT id, entity_name, agent_id "
            "FROM nodes "
            "WHERE invalid_at IS NULL AND deleted_at IS NULL "
            "ORDER BY created_at ASC"
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        # KùzuDB COPY FROM expects a header row matching the table columns
        writer.writerow(["id", "name", "agent_id"])
        for row in rows:
            writer.writerow([row["id"], row["entity_name"], row["agent_id"]])

    logger.info("EXTRACT_NODES | %d rows → %s", len(rows), csv_path)
    return len(rows)


def extract_edges(sqlite_path: str, csv_path: str) -> int:
    """Export active edges from SQLite to a CSV file for KùzuDB COPY.

    CSV columns: source_id (FROM), target_id (TO), weight, updated_at, agent_id
    For REL tables, the first two columns MUST be the source and
    destination primary keys.

    Returns:
        Number of rows exported.
    """
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    rows = []
    try:
        # Check if edges table exists (might be absent in modern schema)
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='edges'"
        ).fetchone()

        if not table_check:
            logger.warning(
                "Table 'edges' does not exist in SQLite (modern schema). Skipping edge extraction."
            )
        else:
            cursor = conn.execute(
                "SELECT source_id, target_id, weight, created_at, agent_id "
                "FROM edges "
                "WHERE invalid_at IS NULL "
                "ORDER BY created_at ASC"
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        # KùzuDB REL COPY: first 2 cols = FROM PK, TO PK, then properties
        writer.writerow(["from", "to", "weight", "updated_at", "agent_id"])
        for row in rows:
            writer.writerow(
                [
                    row["source_id"],
                    row["target_id"],
                    row["weight"],
                    # Convert ISO-8601 string to KùzuDB TIMESTAMP format
                    _normalize_timestamp(row["created_at"]),
                    row["agent_id"],
                ]
            )

    logger.info("EXTRACT_EDGES | %d rows → %s", len(rows), csv_path)
    return len(rows)


def _normalize_timestamp(iso_str: str | None) -> str:
    """Convert an ISO-8601 timestamp to KùzuDB-compatible format.

    KùzuDB expects ``YYYY-MM-DD HH:MM:SS`` (no 'T' separator, no 'Z').
    """
    if not iso_str:
        return "1970-01-01 00:00:00"
    # Replace the 'T' separator and strip trailing 'Z' or timezone offset
    normalized = iso_str.replace("T", " ")
    # Strip timezone suffixes: 'Z', '+00:00', etc.
    for suffix in ("Z", "+00:00", "+0000"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
    # Truncate microseconds if present (KùzuDB TIMESTAMP is second precision)
    if "." in normalized:
        normalized = normalized.split(".")[0]
    return normalized


# ---------------------------------------------------------------------------
# Phase 2: Bulk COPY into KùzuDB
# ---------------------------------------------------------------------------


def bulk_import(
    kuzu_path: str,
    nodes_csv: str,
    edges_csv: str,
) -> tuple[int, int]:
    """Execute KùzuDB native bulk COPY FROM CSV files.

    Args:
        kuzu_path: Path to the KùzuDB database directory.
        nodes_csv: Absolute path to the Entity CSV file.
        edges_csv: Absolute path to the Observed CSV file.
    Returns:
        Verified ``(node_count, edge_count)`` in the staging graph.
    """
    db = kuzu.Database(kuzu_path)
    conn = kuzu.Connection(db)

    try:
        # Ensure schema exists
        conn.execute(_DDL_ENTITY)
        conn.execute(_DDL_OBSERVED)
        logger.info("Schema verified (Entity + Observed tables ready).")

        # --- COPY nodes first (REL table depends on nodes existing) ---
        t0 = time.perf_counter()
        conn.execute(f"COPY Entity FROM '{nodes_csv}' (header=true)")
        node_elapsed = time.perf_counter() - t0
        logger.info("COPY Entity | completed in %.3fs", node_elapsed)

        # --- COPY edges ---
        t1 = time.perf_counter()
        conn.execute(f"COPY Observed FROM '{edges_csv}' (header=true)")
        edge_elapsed = time.perf_counter() - t1
        logger.info("COPY Observed | completed in %.3fs", edge_elapsed)

        # --- Verify row counts ---
        node_count_result = conn.execute("MATCH (n:Entity) RETURN count(n)")
        if isinstance(node_count_result, list):
            node_count_result = node_count_result[0]

        node_count = 0
        while node_count_result.has_next():
            row = node_count_result.get_next()
            node_count = row["count(n)"] if isinstance(row, dict) else row[0]

        edge_count_result = conn.execute("MATCH ()-[r:Observed]->() RETURN count(r)")
        if isinstance(edge_count_result, list):
            edge_count_result = edge_count_result[0]

        edge_count = 0
        while edge_count_result.has_next():
            row = edge_count_result.get_next()
            edge_count = row["count(r)"] if isinstance(row, dict) else row[0]

        logger.info(
            "VERIFICATION | Entity nodes: %d, Observed edges: %d",
            node_count,
            edge_count,
        )
        return node_count, edge_count

    finally:
        conn.close()
        db.close()


def _fingerprint_file(path: Path) -> str:
    """Return a deterministic source fingerprint without copying source data."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate MESA graph data from SQLite to KùzuDB.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--sqlite-db",
        default="./storage/mesa.db",
        help="Path to the legacy SQLite database (default: ./storage/mesa.db)",
    )
    parser.add_argument(
        "--kuzu-db",
        default="./storage/kuzu_db",
        help="Path to the KùzuDB database (default: ./storage/kuzu_db)",
    )
    parser.add_argument(
        "--csv-dir",
        default="./storage/migration_csv",
        help="Directory for intermediate CSV files (default: ./storage/migration_csv)",
    )
    parser.add_argument(
        "--wipe",
        action="store_true",
        help="Refused: live Kùzu directories are never wiped in place.",
    )
    parser.add_argument(
        "--migration-id",
        default="sqlite-edges-to-kuzu-v1",
        help="Immutable operator-selected migration identifier.",
    )
    parser.add_argument(
        "--target-version",
        default="1",
        help="Target graph layout version recorded with the migration.",
    )
    parser.add_argument(
        "--journal-db",
        default=None,
        help="SQLite journal path; defaults beside the live Kùzu directory.",
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="Explicitly restore the retained pre-promotion directory for migration-id.",
    )
    args = parser.parse_args()

    if args.wipe:
        parser.error("--wipe is refused; create a new staged migration instead")

    live_path = Path(args.kuzu_db).resolve()
    journal_path = (
        Path(args.journal_db).resolve()
        if args.journal_db
        else live_path.parent / "kuzu_migration_journal.db"
    )
    coordinator = KuzuMigrationCoordinator(live_path, journal_path)

    if args.rollback:
        outcome = coordinator.rollback(args.migration_id)
        logger.warning(
            "MIGRATION ROLLBACK COMPLETE | id=%s retained_promoted=%s",
            outcome.migration_id,
            outcome.backup_path,
        )
        return

    # Validate SQLite path
    if not os.path.isfile(args.sqlite_db):
        logger.error("SQLite database not found: %s", args.sqlite_db)
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("MESA SQLite → KùzuDB Migration")
    logger.info("=" * 60)
    logger.info("SQLite : %s", args.sqlite_db)
    logger.info("KùzuDB live: %s", live_path)
    logger.info("Migration ID: %s", args.migration_id)
    logger.info("Journal: %s", journal_path)
    logger.info("-" * 60)

    # ---- Phase 1: Extract ----
    total_start = time.perf_counter()

    source_path = Path(args.sqlite_db).resolve()
    csv_root = Path(args.csv_dir).resolve()
    expected_counts: tuple[int, int] | None = None

    def build_staging(staging_path: Path) -> None:
        nonlocal expected_counts
        csv_dir = csv_root / staging_path.name
        csv_dir.mkdir(parents=True, exist_ok=True)
        nodes_csv = str((csv_dir / "migration_nodes.csv").resolve())
        edges_csv = str((csv_dir / "migration_edges.csv").resolve())
        node_count = extract_nodes(str(source_path), nodes_csv)
        edge_count = extract_edges(str(source_path), edges_csv)
        imported_counts = bulk_import(str(staging_path), nodes_csv, edges_csv)
        if imported_counts != (node_count, edge_count):
            raise RuntimeError(
                "staging Kùzu verification count differs from extracted SQLite data"
            )
        expected_counts = imported_counts

    def validate_staging(staging_path: Path) -> None:
        if expected_counts is None:
            # A resumed staging build is self-contained; the bulk import already
            # checked its source counts before journal state became STAGED.
            if not staging_path.is_dir():
                raise RuntimeError("resumable staging directory is missing")

    outcome = coordinator.run(
        migration_id=args.migration_id,
        source_fingerprint=_fingerprint_file(source_path),
        target_version=args.target_version,
        build_staging=build_staging,
        validate_staging=validate_staging,
    )

    total_elapsed = time.perf_counter() - total_start

    logger.info("-" * 60)
    logger.info(
        "MIGRATION COMPLETE | state=%s token=%d in %.3fs "
        "(live=%s retained_previous=%s)",
        outcome.state,
        outcome.fencing_token,
        total_elapsed,
        outcome.live_path,
        outcome.backup_path,
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
