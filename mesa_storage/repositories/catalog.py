"""Tenant/workspace/dataset catalog persistence boundary."""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from mesa_storage.sqlite_engine import AsyncEngine

_CATALOG_ID_NAMESPACE = uuid.UUID("5a227c0d-26ee-47db-98f8-48545636143f")


class CatalogRepositoryPort(Protocol):
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

    async def resolve_id_in_tx(
        self,
        db: Any,
        *,
        tenant_id: str,
        kind: str,
        external_id: str,
        create: bool = False,
    ) -> str: ...

    async def external_id_in_tx(
        self, db: Any, *, tenant_id: str, kind: str, physical_id: str
    ) -> str: ...


class CatalogRepository:
    """Own the atomic SQLite catalog hierarchy and collision checks."""

    __slots__ = ("_sql",)

    def __init__(self, sqlite_engine: AsyncEngine) -> None:
        self._sql = sqlite_engine

    async def resolve_id_in_tx(
        self,
        db: Any,
        *,
        tenant_id: str,
        kind: str,
        external_id: str,
        create: bool = False,
    ) -> str:
        """Resolve one tenant-scoped public ID to its opaque physical key."""
        async with db.execute(
            "SELECT physical_id FROM v4_catalog_identities "
            "WHERE tenant_id = ? AND kind = ? AND external_id = ?",
            (tenant_id, kind, external_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is not None:
            return str(row[0])
        async with db.execute(
            "SELECT physical_id FROM v4_catalog_identities "
            "WHERE tenant_id = ? AND kind = ? AND physical_id = ?",
            (tenant_id, kind, external_id),
        ) as cursor:
            physical = await cursor.fetchone()
        if physical is not None:
            return str(physical[0])
        if not create:
            return external_id
        async with db.execute(
            "SELECT tenant_id FROM v4_catalog_identities "
            "WHERE kind = ? AND physical_id = ?",
            (kind, external_id),
        ) as cursor:
            claimed = await cursor.fetchone()
        physical_id = external_id
        if claimed is not None:
            physical_id = (
                "mesa-"
                + kind
                + "-"
                + uuid.uuid5(
                    _CATALOG_ID_NAMESPACE,
                    f"{kind}\x1f{tenant_id}\x1f{external_id}",
                ).hex
            )
        await db.execute(
            "INSERT INTO v4_catalog_identities "
            "(tenant_id, kind, external_id, physical_id) VALUES (?, ?, ?, ?)",
            (tenant_id, kind, external_id, physical_id),
        )
        return physical_id

    async def external_id_in_tx(
        self, db: Any, *, tenant_id: str, kind: str, physical_id: str
    ) -> str:
        async with db.execute(
            "SELECT external_id FROM v4_catalog_identities "
            "WHERE tenant_id = ? AND kind = ? AND physical_id = ?",
            (tenant_id, kind, physical_id),
        ) as cursor:
            row = await cursor.fetchone()
        return str(row[0]) if row is not None else physical_id

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
        external_workspace_id = workspace_id
        async with self._sql.transaction() as db:
            await db.execute(
                "INSERT OR IGNORE INTO tenants (tenant_id, display_name) VALUES (?, ?)",
                (tenant_id, tenant_name or tenant_id),
            )
            workspace_id = await self.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="workspace",
                external_id=external_workspace_id,
                create=True,
            )
            await db.execute(
                "INSERT OR IGNORE INTO workspaces "
                "(workspace_id, tenant_id, name) VALUES (?, ?, ?)",
                (
                    workspace_id,
                    tenant_id,
                    workspace_name or external_workspace_id,
                ),
            )
            async with db.execute(
                "SELECT * FROM workspaces WHERE workspace_id = ?",
                (workspace_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None or row["tenant_id"] != tenant_id:
                raise ValueError("workspace identity collides with another tenant")
            await db.commit()
        result = dict(row)
        result["workspace_id"] = external_workspace_id
        return result

    async def list_workspaces(self, *, tenant_id: str) -> list[dict[str, Any]]:
        async with self._sql.connection() as db:
            async with db.execute(
                "SELECT i.external_id AS workspace_id, w.tenant_id, w.name, "
                "w.status, w.created_at FROM workspaces w "
                "JOIN v4_catalog_identities i ON i.tenant_id = w.tenant_id "
                "AND i.kind = 'workspace' AND i.physical_id = w.workspace_id "
                "WHERE w.tenant_id = ? ORDER BY w.name, i.external_id",
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
        external_workspace_id = workspace_id
        external_dataset_id = dataset_id
        async with self._sql.transaction() as db:
            await db.execute(
                "INSERT OR IGNORE INTO tenants (tenant_id, display_name) VALUES (?, ?)",
                (tenant_id, tenant_name or tenant_id),
            )
            workspace_id = await self.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="workspace",
                external_id=external_workspace_id,
                create=True,
            )
            dataset_id = await self.resolve_id_in_tx(
                db,
                tenant_id=tenant_id,
                kind="dataset",
                external_id=external_dataset_id,
                create=True,
            )
            await db.execute(
                "INSERT OR IGNORE INTO workspaces "
                "(workspace_id, tenant_id, name) VALUES (?, ?, ?)",
                (
                    workspace_id,
                    tenant_id,
                    workspace_name or external_workspace_id,
                ),
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
                (
                    dataset_id,
                    tenant_id,
                    workspace_id,
                    dataset_name or external_dataset_id,
                ),
            )
            async with db.execute(
                "SELECT tenant_id, workspace_id FROM datasets WHERE dataset_id = ?",
                (dataset_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None or row[0] != tenant_id or row[1] != workspace_id:
                raise ValueError("dataset identity collides with another catalog scope")
            await db.commit()
