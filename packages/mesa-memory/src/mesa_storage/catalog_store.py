"""Deep tenant/workspace/dataset catalog storage module."""

from __future__ import annotations

from typing import Any, Protocol

from mesa_storage.sqlite_engine import AsyncEngine


class CatalogStorePort(Protocol):
    """Stable catalog operations required by API admission and ingestion."""

    async def create_workspace(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        tenant_name: str | None = None,
        workspace_name: str | None = None,
    ) -> dict[str, Any]: ...

    async def list_workspaces(self, *, tenant_id: str) -> list[dict[str, Any]]: ...

    async def ensure_scope(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
        tenant_name: str | None = None,
        workspace_name: str | None = None,
        dataset_name: str | None = None,
    ) -> None: ...


class CatalogStore:
    """Own the atomic SQLite catalog hierarchy and collision checks."""

    __slots__ = ("_sql",)

    def __init__(self, sqlite_engine: AsyncEngine) -> None:
        self._sql = sqlite_engine

    async def create_workspace(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        tenant_name: str | None = None,
        workspace_name: str | None = None,
    ) -> dict[str, Any]:
        """Create one workspace without allowing cross-tenant ID reuse."""
        if not tenant_id or not workspace_id:
            raise ValueError("tenant and workspace identifiers are required")
        async with self._sql.transaction() as db:
            await db.execute(
                "INSERT OR IGNORE INTO tenants (tenant_id, display_name) VALUES (?, ?)",
                (tenant_id, tenant_name or tenant_id),
            )
            await db.execute(
                "INSERT OR IGNORE INTO workspaces "
                "(workspace_id, tenant_id, name) VALUES (?, ?, ?)",
                (workspace_id, tenant_id, workspace_name or workspace_id),
            )
            async with db.execute(
                "SELECT * FROM workspaces WHERE workspace_id = ?",
                (workspace_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None or row["tenant_id"] != tenant_id:
                raise ValueError("workspace identity collides with another tenant")
            await db.commit()
        return dict(row)

    async def list_workspaces(self, *, tenant_id: str) -> list[dict[str, Any]]:
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT workspace_id, tenant_id, name, status, created_at "
                "FROM workspaces WHERE tenant_id = ? ORDER BY name, workspace_id",
                (tenant_id,),
            ) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def ensure_scope(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
        tenant_name: str | None = None,
        workspace_name: str | None = None,
        dataset_name: str | None = None,
    ) -> None:
        """Idempotently provision one tenant/workspace/dataset hierarchy."""
        if not tenant_id or not workspace_id or not dataset_id:
            raise ValueError("tenant, workspace and dataset identifiers are required")
        async with self._sql.transaction() as db:
            await db.execute(
                "INSERT OR IGNORE INTO tenants (tenant_id, display_name) VALUES (?, ?)",
                (tenant_id, tenant_name or tenant_id),
            )
            await db.execute(
                "INSERT OR IGNORE INTO workspaces "
                "(workspace_id, tenant_id, name) VALUES (?, ?, ?)",
                (workspace_id, tenant_id, workspace_name or workspace_id),
            )
            async with db.execute(
                "SELECT tenant_id FROM workspaces WHERE workspace_id = ?",
                (workspace_id,),
            ) as cursor:
                workspace = await cursor.fetchone()
            if workspace is None or workspace[0] != tenant_id:
                raise ValueError("workspace identity collides with another tenant")
            await db.execute(
                "INSERT OR IGNORE INTO datasets "
                "(dataset_id, tenant_id, workspace_id, name) VALUES (?, ?, ?, ?)",
                (dataset_id, tenant_id, workspace_id, dataset_name or dataset_id),
            )
            async with db.execute(
                "SELECT tenant_id, workspace_id FROM datasets WHERE dataset_id = ?",
                (dataset_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None or row[0] != tenant_id or row[1] != workspace_id:
                raise ValueError("dataset identity collides with another catalog scope")
            await db.commit()


# Compatibility names retained until MESA 0.10.
CatalogRepositoryPort = CatalogStorePort
CatalogRepository = CatalogStore
