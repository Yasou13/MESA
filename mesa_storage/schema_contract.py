"""Fail-closed SQLite schema contracts for Alembic-managed MESA storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, cast

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection

BASE_REVISION = "4933fb5fd0ea"

_CORE_MANAGED_TABLES = frozenset({"raw_logs"})
_V4_HEAD_TABLES = frozenset(
    {
        "raw_logs",
        "memory_mutations",
        "projection_outbox",
        "documents",
        "document_revisions",
        "source_chunks",
        "v4_entities",
        "v4_assertions",
        "v4_assertions_fts",
        "v4_catalog_identities",
        "artifact_registry",
        "artifact_sources",
        "artifact_cleanup_outbox",
        "system_operations",
        "projection_generations",
        "projection_runtime",
    }
)


class SchemaContractError(RuntimeError):
    """A database does not meet the MESA migration contract."""


@dataclass(frozen=True)
class ColumnDefinition:
    declared_type: str
    not_null: bool
    default: str | None
    primary_key_position: int


@dataclass(frozen=True)
class SchemaSnapshot:
    tables: frozenset[str]
    columns: dict[str, frozenset[str]]
    column_details: dict[str, dict[str, ColumnDefinition]]
    indexes: frozenset[str]
    triggers: frozenset[str]
    object_sql: dict[str, str]


def _rows(connection: Any, statement: str) -> list[tuple[object, ...]]:
    if hasattr(connection, "exec_driver_sql"):
        cursor = connection.exec_driver_sql(statement)
    else:
        cursor = connection.execute(statement)
    return [tuple(row) for row in cursor.fetchall()]


def inspect_schema(connection: Connection) -> SchemaSnapshot:
    """Return a structural snapshot without reading application data."""
    rows = _rows(
        connection,
        "SELECT name, type, COALESCE(sql, '') FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%'",
    )
    tables = frozenset(str(row[0]) for row in rows if row[1] == "table")
    object_sql = {str(row[0]): str(row[2]) for row in rows}
    columns: dict[str, frozenset[str]] = {}
    column_details: dict[str, dict[str, ColumnDefinition]] = {}
    for table in _V4_HEAD_TABLES | {"alembic_version"}:
        if table in tables:
            table_info = _rows(connection, f'PRAGMA table_info("{table}")')
            columns[table] = frozenset(str(row[1]) for row in table_info)
            column_details[table] = {
                str(row[1]): ColumnDefinition(
                    declared_type=str(row[2]).upper(),
                    not_null=bool(row[3]),
                    default=None if row[4] is None else str(row[4]),
                    primary_key_position=int(cast(int, row[5])),
                )
                for row in table_info
            }
    return SchemaSnapshot(
        tables=tables,
        columns=columns,
        column_details=column_details,
        indexes=frozenset(str(row[0]) for row in rows if row[1] == "index"),
        triggers=frozenset(str(row[0]) for row in rows if row[1] == "trigger"),
        object_sql=object_sql,
    )


def _require_members(
    found: Iterable[str], expected: Iterable[str], *, label: str
) -> None:
    missing = sorted(set(expected) - set(found))
    if missing:
        raise SchemaContractError(
            f"MESA schema drift: missing {label}: {', '.join(missing)}"
        )


def _assert_v4_head_contract(snapshot: SchemaSnapshot) -> None:
    _require_members(snapshot.tables, _V4_HEAD_TABLES, label="V4 head tables")
    fts_sql = snapshot.object_sql.get("v4_assertions_fts", "").lower()
    if "virtual table" not in fts_sql or "fts5" not in fts_sql:
        raise SchemaContractError(
            "MESA schema drift: v4_assertions_fts is not the expected FTS5 table."
        )


def preflight_schema(connection: Connection, alembic_config: Config) -> bool:
    """Validate a SQLite schema before Alembic applies migrations."""
    if connection.dialect.name != "sqlite":
        raise SchemaContractError(
            "MESA schema contract currently supports SQLite only."
        )

    snapshot = inspect_schema(connection)
    if "alembic_version" in snapshot.tables:
        versions = _rows(connection, "SELECT version_num FROM alembic_version")
        if len(versions) != 1 or not versions[0][0]:
            connection.rollback()
            raise SchemaContractError(
                "MESA schema drift: invalid alembic_version state."
            )
        try:
            _require_members(
                snapshot.tables, _CORE_MANAGED_TABLES, label="managed tables"
            )
            expected_head = ScriptDirectory.from_config(alembic_config).get_current_head()
            if versions[0][0] == expected_head:
                _assert_v4_head_contract(snapshot)
        finally:
            if connection.in_transaction():
                connection.rollback()
        return False

    if not snapshot.tables:
        if connection.in_transaction():
            connection.rollback()
        return False

    if connection.in_transaction():
        connection.rollback()
    raise SchemaContractError(
        "MESA schema drift: unmanaged database has no alembic_version. "
        "No Alembic revision was written."
    )


def validate_postflight(
    connection: Connection, alembic_config: Config, *, require_head: bool = False
) -> None:
    """Verify the managed schema after Alembic has completed."""
    snapshot = inspect_schema(connection)
    if "alembic_version" not in snapshot.tables:
        raise SchemaContractError("MESA schema postflight: alembic_version is missing.")
    _require_members(snapshot.tables, _CORE_MANAGED_TABLES, label="managed tables")
    versions = _rows(connection, "SELECT version_num FROM alembic_version")
    if len(versions) != 1 or not versions[0][0]:
        raise SchemaContractError("MESA schema postflight: invalid revision state.")
    expected_head: str | None = None
    if require_head or alembic_config.get_main_option("script_location"):
        expected_head = ScriptDirectory.from_config(alembic_config).get_current_head()
    if require_head and versions[0][0] != expected_head:
        raise SchemaContractError(
            "MESA schema postflight: revision is not at head."
        )
    integrity = _rows(connection, "PRAGMA integrity_check")
    if integrity != [("ok",)]:
        raise SchemaContractError("MESA schema postflight: integrity_check failed.")
    if _rows(connection, "PRAGMA foreign_key_check"):
        raise SchemaContractError("MESA schema postflight: foreign key check failed.")
    if (expected_head is not None and versions[0][0] == expected_head) or (
        "document_revisions" in snapshot.tables
        and "nodes" not in snapshot.tables
    ):
        _assert_v4_head_contract(snapshot)
        revision_columns = {
            str(row[1])
            for row in _rows(connection, "PRAGMA table_info(document_revisions)")
        }
        _require_members(
            revision_columns,
            {"manifest_hash", "manifest_frozen_at", "declared_content_hash"},
            label="revision manifest columns",
        )
        if versions[0][0] == expected_head:
            _require_members(revision_columns, {"chunking_plan"}, label="revision admission columns")
        content_hash = next(
            row
            for row in _rows(connection, "PRAGMA table_info(document_revisions)")
            if str(row[1]) == "content_hash"
        )
        if content_hash[3] != 0:
            raise SchemaContractError(
                "document_revisions.content_hash must allow unknown NULL values"
            )
        _require_members(
            snapshot.indexes,
            {"uq_active_document_revision"},
            label="ACTIVE document-head indexes",
        )
        dup_active = _rows(
            connection,
            "SELECT document_id FROM document_revisions WHERE status = 'ACTIVE' "
            "GROUP BY document_id HAVING count(*) > 1",
        )
        if dup_active:
            raise SchemaContractError(
                "MESA schema postflight: duplicate ACTIVE document heads detected."
            )
        _rows(connection, "SELECT count(*) FROM v4_assertions_fts")
