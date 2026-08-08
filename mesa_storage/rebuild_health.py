"""Content-free durable health view for projection rebuild operations."""

from __future__ import annotations

import json
from typing import Any

from mesa_storage.sqlite_engine import AsyncEngine

REBUILD_OPERATION_STATES = (
    "IDLE",
    "PENDING",
    "CLAIMED",
    "RUNNING",
    "VERIFYING",
    "READY_TO_CUTOVER",
    "COMPLETED",
    "RETRYABLE_FAILED",
    "FINAL_FAILED",
    "CANCELLED",
)
_MAINTENANCE_STATES = frozenset(REBUILD_OPERATION_STATES[1:6]) | {"RETRYABLE_FAILED"}


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        result = int(value)
    except (TypeError, ValueError):
        return 0
    return min(max(result, 0), 2**63 - 1)


class RebuildHealthReader:
    """Read a bounded metrics snapshot without exposing operation identities."""

    __slots__ = ("_sql",)

    def __init__(self, sqlite_engine: AsyncEngine) -> None:
        self._sql = sqlite_engine

    async def snapshot(self) -> dict[str, Any]:
        async with self._sql.connection() as db:
            cursor = await db.execute(
                "SELECT state, progress_completed, progress_total, checkpoint_json, "
                "CAST(MAX(0, (julianday(CASE WHEN state IN "
                "('COMPLETED', 'FINAL_FAILED', 'CANCELLED') THEN "
                "COALESCE(completed_at, cancelled_at, updated_at) ELSE "
                "CURRENT_TIMESTAMP END) - julianday(created_at)) * 86400) AS INTEGER) "
                "AS duration_seconds FROM system_operations "
                "WHERE operation_kind = 'PROJECTION_REBUILD' "
                "ORDER BY CASE WHEN state IN "
                "('PENDING', 'CLAIMED', 'RUNNING', 'VERIFYING', "
                "'READY_TO_CUTOVER', 'RETRYABLE_FAILED') THEN 0 ELSE 1 END, "
                "updated_at DESC, operation_id DESC LIMIT 1"
            )
            latest = await cursor.fetchone()
            cursor = await db.execute(
                "SELECT checkpoint_json FROM system_operations "
                "WHERE operation_kind = 'PROJECTION_REBUILD'"
            )
            checkpoints = await cursor.fetchall()

        rollback_count = 0
        for row in checkpoints:
            try:
                checkpoint = json.loads(row[0])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(checkpoint, dict):
                rollback_count += _nonnegative_int(checkpoint.get("rollback_count"))

        if latest is None:
            return {
                "status": "healthy",
                "state": "IDLE",
                "duration_seconds": 0,
                "progress": {"completed": 0, "total": 0},
                "parity_missing": 0,
                "staging_bytes": 0,
                "rollback_count": rollback_count,
            }

        state = str(latest[0])
        try:
            checkpoint = json.loads(latest[3])
        except (TypeError, ValueError, json.JSONDecodeError):
            checkpoint = {}
        if not isinstance(checkpoint, dict):
            checkpoint = {}
        parity = checkpoint.get("post_cutover") or checkpoint.get("parity") or {}
        if not isinstance(parity, dict):
            parity = {}
        if state in _MAINTENANCE_STATES:
            status = "maintenance"
        elif state == "FINAL_FAILED":
            status = "degraded"
        else:
            status = "healthy"
        return {
            "status": status,
            "state": state if state in REBUILD_OPERATION_STATES else "IDLE",
            "duration_seconds": _nonnegative_int(latest[4]),
            "progress": {
                "completed": _nonnegative_int(latest[1]),
                "total": _nonnegative_int(latest[2]),
            },
            "parity_missing": _nonnegative_int(parity.get("missing")),
            "staging_bytes": _nonnegative_int(checkpoint.get("staging_bytes")),
            "rollback_count": rollback_count,
        }
