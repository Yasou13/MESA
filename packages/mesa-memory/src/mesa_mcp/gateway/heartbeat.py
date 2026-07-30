"""Heartbeat and connection lifecycle management for HTTP Gateway."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from mesa_storage.control.connection_repo import ConnectionRepository

logger = logging.getLogger("MESA_Heartbeat")


class HeartbeatMonitor:
    def __init__(
        self,
        conn_repo: ConnectionRepository,
        interval_seconds: int = 30,
        timeout_seconds: int = 90,
    ):
        self.conn_repo = conn_repo
        self.interval_seconds = interval_seconds
        self.timeout_seconds = timeout_seconds
        self._running = False
        self._task: asyncio.Task | None = None
        self._heartbeats: dict[str, datetime] = (
            {}
        )  # connection_id -> last_heartbeat_time

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            f"Heartbeat monitor started (interval: {self.interval_seconds}s, timeout: {self.timeout_seconds}s)"
        )

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Heartbeat monitor stopped")

    async def _loop(self):
        while self._running:
            try:
                await asyncio.sleep(self.interval_seconds)
                await self._cleanup_stale_connections()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in heartbeat loop: {e}", exc_info=True)

    async def _cleanup_stale_connections(self):
        """Mark connections that haven't sent a heartbeat as DISCONNECTED."""
        now = datetime.now(timezone.utc)
        timeout_threshold = now - timedelta(seconds=self.timeout_seconds)

        stale_conns = []
        for conn_id, last_hb in list(self._heartbeats.items()):
            if last_hb < timeout_threshold:
                stale_conns.append(conn_id)
                del self._heartbeats[conn_id]

        if stale_conns:
            async with self.conn_repo._sql.transaction() as db:
                for chunk in [
                    stale_conns[i : i + 50] for i in range(0, len(stale_conns), 50)
                ]:
                    marks = ",".join(["?"] * len(chunk))
                    await db.execute(
                        f"UPDATE mcp_connections SET status = 'DISCONNECTED' WHERE connection_id IN ({marks})",
                        chunk,
                    )
                await db.commit()
            logger.info(
                f"Marked {len(stale_conns)} connections as DISCONNECTED due to heartbeat timeout."
            )

    async def record_heartbeat(self, connection_id: str):
        now = datetime.now(timezone.utc)
        self._heartbeats[connection_id] = now

        # We optionally ensure the DB knows it is connected if it was previously disconnected
        async with self.conn_repo._sql.transaction() as db:
            await db.execute(
                """
                UPDATE mcp_connections
                SET status = 'CONNECTED'
                WHERE connection_id = ? AND status != 'CONNECTED'
                """,
                (connection_id,),
            )
            await db.commit()
