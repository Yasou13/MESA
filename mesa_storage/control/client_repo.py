from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from mesa_storage.sqlite_engine import AsyncEngine

logger = logging.getLogger(__name__)


class ClientRepository:
    def __init__(self, sqlite_engine: AsyncEngine):
        self._sql = sqlite_engine

    async def create_client(
        self,
        client_id: str,
        display_name: str,
        client_type: str,
        principal_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(metadata or {})
        async with self._sql.transaction() as db:
            await db.execute(
                """
                INSERT INTO mcp_clients (
                    client_id, display_name, client_type, principal_id,
                    created_at, updated_at, metadata_json
                ) VALUES (
                    :client_id, :display_name, :client_type, :principal_id,
                    :now, :now, :metadata_json
                )
                """,
                {
                    "client_id": client_id,
                    "display_name": display_name,
                    "client_type": client_type,
                    "principal_id": principal_id,
                    "now": now,
                    "metadata_json": metadata_json,
                },
            )
            await db.commit()

    async def get_client(self, client_id: str) -> dict[str, Any] | None:
        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(
                "SELECT * FROM mcp_clients WHERE client_id = :client_id",
                {"client_id": client_id},
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                d = dict(row)
                d["metadata"] = json.loads(d["metadata_json"])
                return d

    async def update_client(self, client_id: str, **kwargs: Any) -> None:
        if not kwargs:
            return
        now = datetime.now(timezone.utc).isoformat()
        kwargs["updated_at"] = now

        sets = []
        for k in kwargs.keys():
            if k == "client_id":
                continue
            sets.append(f"{k} = :{k}")

        query = f"UPDATE mcp_clients SET {', '.join(sets)} WHERE client_id = :client_id"
        kwargs["client_id"] = client_id

        async with self._sql.transaction() as db:
            await db.execute(query, kwargs)
            await db.commit()

    async def list_clients(self) -> list[dict[str, Any]]:
        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(
                "SELECT * FROM mcp_clients ORDER BY created_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
                ret = []
                for row in rows:
                    d = dict(row)
                    d["metadata"] = json.loads(d["metadata_json"])
                    ret.append(d)
                return ret

    async def add_project_binding(
        self,
        client_id: str,
        external_project_id: str,
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
    ) -> str:
        binding_id = f"bnd_{client_id}_{external_project_id}"
        async with self._sql.transaction() as db:
            await db.execute(
                """
                INSERT INTO mcp_project_bindings (
                    binding_id, client_id, external_project_id, tenant_id, workspace_id, dataset_id
                ) VALUES (
                    :binding_id, :client_id, :external_project_id, :tenant_id, :workspace_id, :dataset_id
                )
                ON CONFLICT(client_id, external_project_id) DO UPDATE SET
                    tenant_id=excluded.tenant_id,
                    workspace_id=excluded.workspace_id,
                    dataset_id=excluded.dataset_id,
                    enabled=1
                """,
                {
                    "binding_id": binding_id,
                    "client_id": client_id,
                    "external_project_id": external_project_id,
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "dataset_id": dataset_id,
                },
            )
            await db.commit()
        return binding_id

    async def get_project_binding(
        self, client_id: str, external_project_id: str
    ) -> dict[str, Any] | None:
        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(
                """
                SELECT * FROM mcp_project_bindings
                WHERE client_id = :client_id AND external_project_id = :external_project_id AND enabled = 1
                """,
                {"client_id": client_id, "external_project_id": external_project_id},
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return dict(row)

    async def toggle_client_enabled(self, client_id: str, enabled: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._sql.transaction() as db:
            await db.execute(
                "UPDATE mcp_clients SET enabled = :enabled, updated_at = :now WHERE client_id = :client_id",
                {"enabled": 1 if enabled else 0, "now": now, "client_id": client_id},
            )
            await db.commit()

    async def list_bindings(self, client_id: str) -> list[dict[str, Any]]:
        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(
                "SELECT * FROM mcp_project_bindings WHERE client_id = :client_id ORDER BY binding_id ASC",
                {"client_id": client_id},
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
