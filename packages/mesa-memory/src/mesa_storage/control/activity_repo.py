import json
import logging
from datetime import datetime, timezone
from typing import Any

from mesa_storage.sqlite_engine import AsyncEngine

logger = logging.getLogger(__name__)


class ActivityRecorder:
    def __init__(self, sqlite_engine: AsyncEngine):
        self._sql = sqlite_engine

    async def record_call_start(
        self,
        call_id: str,
        trace_id: str,
        client_id: str,
        tool_name: str,
        operation_type: str,
        decision: str,
        connection_id: str | None = None,
        principal_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(metadata or {})
        status = "STARTED" if decision != "DENY" else "DENIED"

        async with self._sql.transaction() as db:
            await db.execute(
                """
                INSERT INTO mcp_tool_calls (
                    call_id, trace_id, connection_id, client_id, principal_id,
                    tool_name, operation_type, decision, status, started_at,
                    metadata_json, request_summary, request_fingerprint
                ) VALUES (
                    :call_id, :trace_id, :connection_id, :client_id, :principal_id,
                    :tool_name, :operation_type, :decision, :status, :started_at,
                    :metadata_json, :request_summary, :request_fingerprint
                )
                """,
                {
                    "call_id": call_id,
                    "trace_id": trace_id,
                    "connection_id": connection_id,
                    "client_id": client_id,
                    "principal_id": principal_id,
                    "tool_name": tool_name,
                    "operation_type": operation_type,
                    "decision": decision,
                    "status": status,
                    "started_at": now,
                    "metadata_json": metadata_json,
                    "request_summary": kwargs.get("request_summary"),
                    "request_fingerprint": kwargs.get("request_fingerprint"),
                },
            )
            await db.commit()

    async def record_call_completion(
        self,
        call_id: str,
        status: str,
        duration_ms: int | None = None,
        error_message: str | None = None,
        **kwargs: Any,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._sql.transaction() as db:
            await db.execute(
                """
                UPDATE mcp_tool_calls
                SET status = :status, completed_at = :now, duration_ms = :duration_ms,
                    error_message = :error_message, memory_id = COALESCE(memory_id, :memory_id),
                    mutation_id = COALESCE(mutation_id, :mutation_id),
                    pipeline_run_id = COALESCE(pipeline_run_id, :pipeline_run_id)
                WHERE call_id = :call_id
                """,
                {
                    "call_id": call_id,
                    "status": status,
                    "now": now,
                    "duration_ms": duration_ms,
                    "error_message": error_message,
                    "memory_id": kwargs.get("memory_id"),
                    "mutation_id": kwargs.get("mutation_id"),
                    "pipeline_run_id": kwargs.get("pipeline_run_id"),
                },
            )
            await db.commit()

    async def get_call(self, call_id: str) -> dict[str, Any] | None:
        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(
                "SELECT * FROM mcp_tool_calls WHERE call_id = :call_id",
                {"call_id": call_id},
            ) as c:
                row = await c.fetchone()
                return dict(row) if row else None

    async def list_recent_calls(
        self,
        limit: int = 50,
        offset: int = 0,
        client_id: str | None = None,
        status: str | None = None,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent tool calls, newest first, with optional filters."""
        query = "SELECT * FROM mcp_tool_calls WHERE 1=1"
        params: dict[str, Any] = {}
        if client_id:
            query += " AND client_id = :client_id"
            params["client_id"] = client_id
        if status:
            query += " AND status = :status"
            params["status"] = status
        if since:
            query += " AND started_at >= :since"
            params["since"] = since
        query += " ORDER BY started_at DESC LIMIT :limit OFFSET :offset"
        params["limit"] = min(limit, 200)
        params["offset"] = offset

        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def list_calls_by_trace(self, trace_id: str) -> list[dict[str, Any]]:
        """Return all tool calls sharing the same trace_id."""
        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(
                "SELECT * FROM mcp_tool_calls WHERE trace_id = :trace_id ORDER BY started_at",
                {"trace_id": trace_id},
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def count_calls_by_status(self, since: str | None = None) -> dict[str, int]:
        """Return call counts grouped by status (for overview widget)."""
        query = "SELECT status, COUNT(*) as cnt FROM mcp_tool_calls"
        params: dict[str, Any] = {}
        if since:
            query += " WHERE started_at >= :since"
            params["since"] = since
        query += " GROUP BY status"

        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return {row["status"]: row["cnt"] for row in rows}
