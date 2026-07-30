"""Durable, binding-scoped MCP context settings."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from mesa_storage.sqlite_engine import AsyncEngine

_DEFAULT_TYPES = ["architecture", "decision", "constraint", "convention"]


class BindingContextProfileRepository:
    def __init__(self, sqlite_engine: AsyncEngine):
        self._sql = sqlite_engine

    async def get(self, binding_id: str) -> dict[str, Any]:
        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(
                "SELECT * FROM mcp_binding_context_profiles WHERE binding_id = ?", (binding_id,)
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return self._default(binding_id)
        return self._decode(dict(row))

    async def ensure(self, binding_id: str) -> dict[str, Any]:
        default = self._default(binding_id)
        async with self._sql.transaction() as db:
            await db.execute(
                """INSERT OR IGNORE INTO mcp_binding_context_profiles
                   (binding_id, session_start_enabled, post_compact_enabled, max_records,
                    max_tokens, memory_types_json, revision)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    binding_id,
                    int(default["session_start_enabled"]),
                    int(default["post_compact_enabled"]),
                    default["max_records"],
                    default["max_tokens"],
                    json.dumps(default["memory_types"]),
                    default["revision"],
                ),
            )
            await db.commit()
        return await self.get(binding_id)

    async def update(self, binding_id: str, **values: Any) -> dict[str, Any]:
        current = await self.ensure(binding_id)
        merged = {
            **current,
            **{key: value for key, value in values.items() if value is not None},
        }
        merged["max_records"] = min(max(int(merged["max_records"]), 1), 8)
        merged["max_tokens"] = min(max(int(merged["max_tokens"]), 1), 2500)
        types = merged["memory_types"]
        if not isinstance(types, list) or not all(
            isinstance(item, str) for item in types
        ):
            raise ValueError("memory_types must be a list of strings")
        now = datetime.now(timezone.utc).isoformat()
        async with self._sql.transaction() as db:
            await db.execute(
                """UPDATE mcp_binding_context_profiles
                   SET session_start_enabled = ?, post_compact_enabled = ?,
                       max_records = ?, max_tokens = ?, memory_types_json = ?,
                       revision = revision + 1, updated_at = ?
                   WHERE binding_id = ?""",
                (
                    int(bool(merged["session_start_enabled"])),
                    int(bool(merged["post_compact_enabled"])),
                    merged["max_records"],
                    merged["max_tokens"],
                    json.dumps(types),
                    now,
                    binding_id,
                ),
            )
            await db.commit()
        return await self.get(binding_id)

    @staticmethod
    def _default(binding_id: str) -> dict[str, Any]:
        return {
            "binding_id": binding_id,
            "session_start_enabled": True,
            "post_compact_enabled": True,
            "max_records": 8,
            "max_tokens": 2500,
            "memory_types": list(_DEFAULT_TYPES),
            "revision": 1,
        }

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        row["session_start_enabled"] = bool(row["session_start_enabled"])
        row["post_compact_enabled"] = bool(row["post_compact_enabled"])
        row["memory_types"] = json.loads(row.pop("memory_types_json"))
        return row


# Compatibility import for callers created before profiles became client-neutral.
CodexProfileRepository = BindingContextProfileRepository
