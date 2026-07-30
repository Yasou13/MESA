import json
import logging
from datetime import datetime, timezone
from typing import Any

from mesa_storage.sqlite_engine import AsyncEngine

logger = logging.getLogger(__name__)


class SettingsRepository:
    def __init__(self, sqlite_engine: AsyncEngine):
        self._sql = sqlite_engine

    async def get_setting(self, setting_key: str) -> Any | None:
        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(
                "SELECT setting_value_json FROM control_plane_settings WHERE setting_key = :key",
                {"key": setting_key},
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return json.loads(row["setting_value_json"])

    async def get_all_settings(self) -> dict[str, Any]:
        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(
                "SELECT setting_key, setting_value_json FROM control_plane_settings"
            ) as cursor:
                rows = await cursor.fetchall()
                return {
                    row["setting_key"]: json.loads(row["setting_value_json"])
                    for row in rows
                }

    async def set_setting(
        self, setting_key: str, value: Any, updated_by: str = "system"
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        value_json = json.dumps(value)
        async with self._sql.transaction() as db:
            await db.execute(
                """
                INSERT INTO control_plane_settings (setting_key, setting_value_json, updated_at, updated_by)
                VALUES (:key, :val, :now, :by)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value_json = excluded.setting_value_json,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                {"key": setting_key, "val": value_json, "now": now, "by": updated_by},
            )
            await db.commit()
