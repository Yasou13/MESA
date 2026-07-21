"""Fail-closed SQLite schema contracts for Alembic-managed MESA storage.

The first Alembic revision used ``CREATE ... IF NOT EXISTS``.  A database
created by an older, pre-Alembic release could therefore be stamped as current
without actually satisfying the first revision's schema.  This module keeps
that adoption explicit and verifies the small set of storage objects owned by
MESA before Alembic advances a revision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, cast

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection

BASE_REVISION = "4933fb5fd0ea"

_BASE_NODE_COLUMNS = frozenset(
    {
        "id",
        "entity_name",
        "type",
        "content_payload",
        "is_consolidated",
        "created_at",
        "invalid_at",
        "deleted_at",
        "agent_id",
        "session_id",
    }
)
_LEGACY_NODE_COLUMNS = _BASE_NODE_COLUMNS - {"content_payload"}
_NODE_INDEXES = frozenset(
    {
        "idx_nodes_active",
        "idx_nodes_entity_name",
        "idx_nodes_agent",
        "idx_nodes_unconsolidated",
        "idx_nodes_soft_deleted",
    }
)
_FTS_TRIGGERS = frozenset(
    {
        "trg_nodes_fts_insert",
        "trg_nodes_fts_delete",
        "trg_nodes_fts_update",
    }
)
_BASE_TABLES = frozenset(
    {
        "nodes",
        "nodes_fts",
        "routing_telemetry",
        "raw_logs",
        "system_config",
        "lancedb_wal",
    }
)
_APPLICATION_TABLES = _BASE_TABLES | {"edges"}


class SchemaContractError(RuntimeError):
    """A database does not meet the MESA migration contract."""


class LegacySchemaAdoptionRequired(SchemaContractError):
    """A recognised pre-Alembic schema needs an explicit offline adoption."""


@dataclass(frozen=True)
class SchemaSnapshot:
    tables: frozenset[str]
    columns: dict[str, frozenset[str]]
    column_details: dict[str, dict[str, "ColumnDefinition"]]
    indexes: frozenset[str]
    triggers: frozenset[str]
    object_sql: dict[str, str]


@dataclass(frozen=True)
class ColumnDefinition:
    declared_type: str
    not_null: bool
    default: str | None
    primary_key_position: int


_BASE_NODE_DEFINITIONS = {
    "id": ColumnDefinition("TEXT", False, None, 1),
    "entity_name": ColumnDefinition("TEXT", True, None, 0),
    "type": ColumnDefinition("TEXT", True, "'ENTITY'", 0),
    "content_payload": ColumnDefinition("TEXT", True, "''", 0),
    "is_consolidated": ColumnDefinition("INTEGER", True, "0", 0),
    "created_at": ColumnDefinition("TEXT", True, None, 0),
    "invalid_at": ColumnDefinition("TEXT", False, "NULL", 0),
    "deleted_at": ColumnDefinition("TEXT", False, "NULL", 0),
    "agent_id": ColumnDefinition("TEXT", True, "'__unset__'", 0),
    "session_id": ColumnDefinition("TEXT", True, "'__unset__'", 0),
}
_LEGACY_NODE_DEFINITIONS = {
    name: definition
    for name, definition in _BASE_NODE_DEFINITIONS.items()
    if name != "content_payload"
}


def _rows(connection: Connection, statement: str) -> list[tuple[object, ...]]:
    return [tuple(row) for row in connection.exec_driver_sql(statement).fetchall()]


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
    for table in _APPLICATION_TABLES | {"alembic_version"}:
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


def _assert_fts(snapshot: SchemaSnapshot) -> None:
    fts_sql = snapshot.object_sql.get("nodes_fts", "").lower()
    if "virtual table" not in fts_sql or "fts5" not in fts_sql:
        raise SchemaContractError(
            "MESA schema drift: nodes_fts is not the expected FTS5 table."
        )
    _require_members(snapshot.triggers, _FTS_TRIGGERS, label="FTS triggers")


def _assert_node_contract(
    snapshot: SchemaSnapshot,
    expected_definitions: dict[str, ColumnDefinition],
    *,
    label: str,
) -> None:
    node_columns = snapshot.columns.get("nodes", frozenset())
    _require_members(node_columns, expected_definitions, label=label)
    definitions = snapshot.column_details.get("nodes", {})
    for name, expected in expected_definitions.items():
        actual = definitions[name]
        if actual.declared_type != expected.declared_type:
            raise SchemaContractError(
                "MESA schema drift: "
                f"nodes.{name} has type {actual.declared_type!r}, expected "
                f"{expected.declared_type!r}."
            )
        if actual.primary_key_position != expected.primary_key_position:
            raise SchemaContractError(
                "MESA schema drift: "
                f"nodes.{name} has unexpected primary-key position."
            )
        if actual.not_null != expected.not_null:
            raise SchemaContractError(
                f"MESA schema drift: nodes.{name} has unexpected NOT NULL contract."
            )
        if actual.default != expected.default:
            raise SchemaContractError(
                "MESA schema drift: "
                f"nodes.{name} has default {actual.default!r}, expected "
                f"{expected.default!r}."
            )


def _assert_base_node_contract(snapshot: SchemaSnapshot) -> None:
    _assert_node_contract(
        snapshot,
        _BASE_NODE_DEFINITIONS,
        label="nodes columns",
    )


def _assert_legacy_node_contract(snapshot: SchemaSnapshot) -> None:
    _assert_node_contract(
        snapshot,
        _LEGACY_NODE_DEFINITIONS,
        label="legacy nodes columns",
    )


def _assert_base_contract(snapshot: SchemaSnapshot) -> None:
    _require_members(snapshot.tables, _BASE_TABLES, label="base tables")
    _assert_base_node_contract(snapshot)
    _require_members(snapshot.indexes, _NODE_INDEXES, label="nodes indexes")
    _assert_fts(snapshot)


def _legacy_family(snapshot: SchemaSnapshot) -> str | None:
    """Recognise only released pre-Alembic schema families.

    Extra non-MESA tables are allowed so applications can keep independent
    metadata in the same SQLite file.  A change to any MESA-owned object is
    not a recognised legacy fingerprint and remains fail-closed.
    """
    node_columns = snapshot.columns.get("nodes")
    if node_columns is None:
        return None
    try:
        _assert_legacy_node_contract(snapshot)
        _require_members(snapshot.indexes, _NODE_INDEXES, label="nodes indexes")
        _assert_fts(snapshot)
    except SchemaContractError:
        return None

    app_tables = snapshot.tables & _APPLICATION_TABLES
    has_legacy_nodes = node_columns == _LEGACY_NODE_COLUMNS
    has_base_nodes = node_columns == _BASE_NODE_COLUMNS
    if has_legacy_nodes and app_tables == {"nodes", "nodes_fts", "edges"}:
        return "v0.3"
    if has_legacy_nodes and app_tables == {
        "nodes",
        "nodes_fts",
        "edges",
        "routing_telemetry",
        "raw_logs",
    }:
        return "v0.4"
    if has_legacy_nodes and app_tables == {
        "nodes",
        "nodes_fts",
        "routing_telemetry",
        "raw_logs",
    }:
        return "v0.5.0"
    if has_base_nodes and app_tables == _BASE_TABLES:
        return "v0.5.1-v0.5.2"
    return None


def _apply_base_bridge(connection: Connection) -> None:
    """Bring a recognised legacy layout to the first Alembic contract."""
    snapshot = inspect_schema(connection)
    if "content_payload" not in snapshot.columns.get("nodes", frozenset()):
        connection.exec_driver_sql(
            "ALTER TABLE nodes ADD COLUMN content_payload TEXT NOT NULL DEFAULT ''"
        )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS routing_telemetry ("
        "id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, record_id TEXT NOT NULL, "
        "small_model_decision INTEGER NOT NULL, small_model_confidence REAL NOT NULL, "
        "dual_llm_decision INTEGER NOT NULL, is_hallucination INTEGER NOT NULL, "
        "created_at TEXT NOT NULL)"
    )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS raw_logs ("
        "id INTEGER PRIMARY KEY, agent_id TEXT NOT NULL, payload JSON NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'DEFERRED', "
        "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_raw_logs_session "
        "ON raw_logs(json_extract(payload, '$.agent_id'), "
        "json_extract(payload, '$.session_id'))"
    )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.exec_driver_sql(
        "INSERT OR IGNORE INTO system_config (key, value) "
        "VALUES ('lancedb_is_migrating', 'false')"
    )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS lancedb_wal ("
        "id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, vector BLOB NOT NULL, "
        "metadata JSON, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )


def _stamp_base_revision(connection: Connection, alembic_config: Config) -> None:
    migration_context = MigrationContext.configure(connection)
    script = ScriptDirectory.from_config(alembic_config)
    migration_context.stamp(script, BASE_REVISION)


def _adopt_legacy_schema(connection: Connection, alembic_config: object) -> None:
    """Atomically bridge and stamp a recognised legacy database.

    The bridge and its base revision are one SQLite ``BEGIN IMMEDIATE`` unit.
    Alembic's async SQLite environment then starts its normal transaction for
    later revisions.  If a later revision fails, the database accurately
    remains at the stamped base revision and can be resumed safely; it is
    never incorrectly stamped as head.
    """
    if connection.in_transaction():
        connection.commit()
    connection.exec_driver_sql("BEGIN IMMEDIATE")
    try:
        _apply_base_bridge(connection)
        _assert_base_contract(inspect_schema(connection))
        _stamp_base_revision(connection, alembic_config)
        connection.commit()
    except Exception:
        if connection.in_transaction():
            connection.rollback()
        raise


def preflight_schema(connection: Connection, alembic_config: object) -> bool:
    """Validate a SQLite schema and optionally adopt a known legacy layout.

    Returns ``True`` only when this invocation adopted a legacy database.
    """
    if connection.dialect.name != "sqlite":
        raise SchemaContractError(
            "MESA schema contract currently supports SQLite only."
        )

    snapshot = inspect_schema(connection)
    app_tables = snapshot.tables & _APPLICATION_TABLES
    if "alembic_version" in snapshot.tables:
        versions = _rows(connection, "SELECT version_num FROM alembic_version")
        if len(versions) != 1 or not versions[0][0]:
            connection.rollback()
            raise SchemaContractError(
                "MESA schema drift: invalid alembic_version state."
            )
        try:
            _assert_base_contract(snapshot)
        finally:
            if connection.in_transaction():
                connection.rollback()
        return False

    if not app_tables:
        if connection.in_transaction():
            connection.rollback()
        return False

    family = _legacy_family(snapshot)
    if family is None:
        if connection.in_transaction():
            connection.rollback()
        raise SchemaContractError(
            "MESA schema drift: unmanaged database does not match a supported "
            "v0.3-v0.5 legacy fingerprint. No Alembic revision was written."
        )

    x_arguments = getattr(getattr(alembic_config, "cmd_opts", None), "x", []) or []
    adopt_requested = "mesa_legacy=adopt" in x_arguments
    if not adopt_requested:
        if connection.in_transaction():
            connection.rollback()
        raise LegacySchemaAdoptionRequired(
            f"Recognised {family} legacy MESA schema. Stop the application, take "
            "an offline backup, then run: alembic -x mesa_legacy=adopt upgrade head"
        )

    _adopt_legacy_schema(connection, alembic_config)
    return True


def validate_postflight(
    connection: Connection, alembic_config: Config, *, require_head: bool = False
) -> None:
    """Verify the managed schema after Alembic has completed."""
    snapshot = inspect_schema(connection)
    if "alembic_version" not in snapshot.tables:
        raise SchemaContractError("MESA schema postflight: alembic_version is missing.")
    _assert_base_contract(snapshot)
    versions = _rows(connection, "SELECT version_num FROM alembic_version")
    if len(versions) != 1 or not versions[0][0]:
        raise SchemaContractError("MESA schema postflight: invalid revision state.")
    if require_head:
        expected_head = ScriptDirectory.from_config(alembic_config).get_current_head()
        if versions[0][0] != expected_head:
            raise SchemaContractError(
                "MESA schema postflight: revision is not at head."
            )
    integrity = _rows(connection, "PRAGMA integrity_check")
    if integrity != [("ok",)]:
        raise SchemaContractError("MESA schema postflight: integrity_check failed.")
    if _rows(connection, "PRAGMA foreign_key_check"):
        raise SchemaContractError("MESA schema postflight: foreign key check failed.")
    connection.exec_driver_sql("SELECT count(*) FROM nodes_fts").scalar_one()
