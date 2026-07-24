# mypy: disable-error-code="no-any-return"
import logging
from datetime import datetime, timezone
from typing import Any

from mesa_storage.sqlite_engine import AsyncEngine

logger = logging.getLogger(__name__)


class ApprovalRepository:
    def __init__(self, sqlite_engine: AsyncEngine):
        self._sql = sqlite_engine

    async def create_approval_request(
        self,
        approval_id: str,
        call_id: str,
        client_id: str,
        operation: str,
        request_summary: str,
        payload_hash: str,
        payload_encrypted: bytes | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        status = "PENDING"
        async with self._sql.transaction() as db:
            await db.execute(
                """
                INSERT INTO mcp_approval_requests (
                    approval_id, call_id, client_id, operation, status,
                    request_summary, payload_hash, payload_encrypted, requested_at
                ) VALUES (
                    :approval_id, :call_id, :client_id, :operation, :status,
                    :request_summary, :payload_hash, :payload_encrypted, :requested_at
                )
                """,
                {
                    "approval_id": approval_id,
                    "call_id": call_id,
                    "client_id": client_id,
                    "operation": operation,
                    "status": status,
                    "request_summary": request_summary,
                    "payload_hash": payload_hash,
                    "payload_encrypted": payload_encrypted,
                    "requested_at": now,
                },
            )
            await db.commit()

    async def get_approval_request(self, approval_id: str) -> dict[str, Any] | None:
        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(
                "SELECT * FROM mcp_approval_requests WHERE approval_id = :aid",
                {"aid": approval_id},
            ) as c:
                row = await c.fetchone()
                return dict(row) if row else None

    async def decide_approval(
        self, approval_id: str, status: str, decided_by: str, reason: str | None = None
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._sql.transaction() as db:
            await db.execute(
                """
                UPDATE mcp_approval_requests
                SET status = :status, decided_at = :now, decided_by = :decided_by, decision_reason = :reason
                WHERE approval_id = :aid
                """,
                {
                    "aid": approval_id,
                    "status": status,
                    "now": now,
                    "decided_by": decided_by,
                    "reason": reason,
                },
            )
            await db.commit()

    async def list_pending_approvals(
        self, client_id: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM mcp_approval_requests WHERE status = 'PENDING'"
        params: dict[str, Any] = {}
        if client_id:
            query += " AND client_id = :client_id"
            params["client_id"] = client_id
        query += " ORDER BY requested_at ASC"

        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def list_approvals(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM mcp_approval_requests WHERE 1=1"
        params: dict[str, Any] = {}
        if status:
            query += " AND status = :status"
            params["status"] = status
        query += " ORDER BY requested_at DESC LIMIT :limit OFFSET :offset"
        params["limit"] = min(limit, 200)
        params["offset"] = offset

        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def count_pending(self) -> int:
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM mcp_approval_requests WHERE status = 'PENDING'"
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def expire_stale_approvals(self, ttl_seconds: int = 86400) -> int:
        now = datetime.now(timezone.utc)
        cutoff = datetime.fromtimestamp(
            now.timestamp() - ttl_seconds, tz=timezone.utc
        ).isoformat()

        query = """
            UPDATE mcp_approval_requests
            SET status = 'EXPIRED', decided_at = :now, decision_reason = 'TTL expired'
            WHERE status = 'PENDING' AND requested_at < :cutoff
        """
        async with self._sql.transaction() as db:
            cursor = await db.execute(query, {"now": now.isoformat(), "cutoff": cutoff})
            await db.commit()
            return cursor.rowcount
