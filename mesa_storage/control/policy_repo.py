import json
import logging
from datetime import datetime, timezone
from typing import Any

from mesa_storage.sqlite_engine import AsyncEngine

logger = logging.getLogger(__name__)


class PolicyRepository:
    def __init__(self, sqlite_engine: AsyncEngine):
        self._sql = sqlite_engine

    async def create_rule(
        self,
        rule_id: str,
        scope_type: str,
        operation: str,
        effect: str,
        created_by: str,
        scope_id: str | None = None,
        priority: int = 100,
        conditions: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        cond_json = json.dumps(conditions or {})

        async with self._sql.transaction() as db:
            await db.execute(
                """
                INSERT INTO mcp_policy_rules (
                    rule_id, scope_type, scope_id, operation, effect,
                    priority, conditions_json, created_by, created_at, updated_at
                ) VALUES (
                    :rule_id, :scope_type, :scope_id, :operation, :effect,
                    :priority, :cond_json, :created_by, :now, :now
                )
                """,
                {
                    "rule_id": rule_id,
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                    "operation": operation,
                    "effect": effect,
                    "priority": priority,
                    "cond_json": cond_json,
                    "created_by": created_by,
                    "now": now,
                },
            )
            await db.commit()

    async def get_rule(self, rule_id: str) -> dict[str, Any] | None:
        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(
                "SELECT * FROM mcp_policy_rules WHERE rule_id = :rule_id",
                {"rule_id": rule_id},
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def list_rules(
        self, scope_type: str | None = None, operation: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM mcp_policy_rules WHERE 1=1"
        params = {}
        if scope_type:
            query += " AND scope_type = :scope_type"
            params["scope_type"] = scope_type
        if operation:
            query += " AND operation = :operation"
            params["operation"] = operation
        query += " ORDER BY priority DESC"

        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
