import logging
from datetime import datetime, timezone
from typing import Any

from mesa_storage.sqlite_engine import AsyncEngine

logger = logging.getLogger(__name__)


class ConnectionRepository:
    def __init__(self, sqlite_engine: AsyncEngine):
        self._sql = sqlite_engine

    async def register_connection(
        self, connection_id: str, client_id: str, transport: str, **kwargs: Any
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        status = kwargs.get("status", "CONNECTED")

        async with self._sql.transaction() as db:
            await db.execute(
                """
                INSERT INTO mcp_connections (
                    connection_id, client_id, transport, status, connected_at, last_seen_at,
                    remote_address_hash, protocol_version, client_version, user_agent, session_id, project_id
                ) VALUES (
                    :connection_id, :client_id, :transport, :status, :now, :now,
                    :remote_address_hash, :protocol_version, :client_version, :user_agent, :session_id, :project_id
                )
                """,
                {
                    "connection_id": connection_id,
                    "client_id": client_id,
                    "transport": transport,
                    "status": status,
                    "now": now,
                    "remote_address_hash": kwargs.get("remote_address_hash"),
                    "protocol_version": kwargs.get("protocol_version"),
                    "client_version": kwargs.get("client_version"),
                    "user_agent": kwargs.get("user_agent"),
                    "session_id": kwargs.get("session_id"),
                    "project_id": kwargs.get("project_id"),
                },
            )
            await db.commit()

    async def update_connection_status(self, connection_id: str, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        disconnected_at = (
            now if status in ("DISCONNECTED", "REVOKED", "ERROR") else None
        )

        async with self._sql.transaction() as db:
            await db.execute(
                """
                UPDATE mcp_connections
                SET status = :status, last_seen_at = :now, disconnected_at = COALESCE(disconnected_at, :disconnected_at)
                WHERE connection_id = :connection_id
                """,
                {
                    "connection_id": connection_id,
                    "status": status,
                    "now": now,
                    "disconnected_at": disconnected_at,
                },
            )
            await db.commit()

    async def get_connection(self, connection_id: str) -> dict[str, Any] | None:
        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(
                "SELECT * FROM mcp_connections WHERE connection_id = :connection_id",
                {"connection_id": connection_id},
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def list_active_connections(
        self, client_id: str | None = None
    ) -> list[dict[str, Any]]:
        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            query = "SELECT * FROM mcp_connections WHERE status = 'CONNECTED'"
            params: dict[str, Any] = {}
            if client_id:
                query += " AND client_id = :client_id"
                params["client_id"] = client_id

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def list_all_connections(
        self, limit: int = 50, status: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM mcp_connections WHERE 1=1"
        params: dict[str, Any] = {}
        if status:
            query += " AND status = :status"
            params["status"] = status
        query += " ORDER BY connected_at DESC LIMIT :limit"
        params["limit"] = min(limit, 200)

        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def count_by_status(self) -> dict[str, int]:
        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(
                "SELECT status, COUNT(*) as cnt FROM mcp_connections GROUP BY status"
            ) as cursor:
                rows = await cursor.fetchall()
                return {row["status"]: row["cnt"] for row in rows}
