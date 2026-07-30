"""Data retention and housekeeping policies."""

import logging

from mesa_storage.sqlite_engine import AsyncEngine

logger = logging.getLogger("MESA_Retention")


class RetentionRepository:
    def __init__(self, sqlite_engine: AsyncEngine):
        self._sql = sqlite_engine

    async def archive_old_activity(self, older_than_days: int = 30) -> int:
        """Move or delete old activity logs. For MVP, we'll just delete them."""
        # SQLite dialect syntax
        async with self._sql.transaction() as db:
            await db.execute(
                """
                DELETE FROM mcp_tool_calls
                WHERE started_at < datetime('now', '-' || :days || ' days')
                """,
                {"days": str(older_than_days)},
            )
            await db.commit()

            # This is a bit of a hack since asyncpg/aiosqlite rowcount differences exist.
            # Assuming we can just return a generic success for now.
            logger.info(
                f"Archived/deleted tool calls older than {older_than_days} days."
            )
            return 1

    async def get_db_stats(self) -> dict:
        """Return counts of various tables to inform retention needs."""
        async with self._sql.transaction() as db:
            c1 = await db.execute("SELECT COUNT(*) FROM mcp_tool_calls")
            calls_row = await c1.fetchone()

            c2 = await db.execute("SELECT COUNT(*) FROM mcp_connections")
            conns_row = await c2.fetchone()

            c3 = await db.execute("SELECT COUNT(*) FROM mcp_clients")
            clients_row = await c3.fetchone()

            return {
                "total_tool_calls": calls_row[0] if calls_row else 0,
                "total_connections": conns_row[0] if conns_row else 0,
                "total_clients": clients_row[0] if clients_row else 0,
            }
