from __future__ import annotations

from types import SimpleNamespace

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.repositories.catalog import CatalogRepository, CatalogRepositoryPort
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.mark.asyncio
async def test_catalog_repository_owns_scope_and_dao_preserves_compatibility(
    tmp_path,
) -> None:
    engine = AsyncEngine(str(tmp_path / "catalog-repository.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    try:
        repository: CatalogRepositoryPort = dao.catalog
        assert isinstance(repository, CatalogRepository)

        workspace = await repository.create_workspace(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            workspace_name="Legal",
        )
        assert workspace["tenant_id"] == "tenant-a"
        await repository.ensure_scope(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            dataset_id="dataset-a",
        )
        assert [
            item["workspace_id"]
            for item in await dao.list_v4_workspaces(tenant_id="tenant-a")
        ] == ["workspace-a"]

        await repository.ensure_scope(
            tenant_id="tenant-b",
            workspace_id="workspace-a",
            dataset_id="dataset-a",
        )
        async with engine.connection() as db:
            async with db.execute(
                "SELECT tenant_id, physical_id FROM v4_catalog_identities "
                "WHERE kind = 'workspace' AND external_id = 'workspace-a' "
                "ORDER BY tenant_id"
            ) as cursor:
                physical_workspaces = await cursor.fetchall()
        assert len(physical_workspaces) == 2
        assert physical_workspaces[0][1] != physical_workspaces[1][1]
        assert [
            item["workspace_id"]
            for item in await dao.list_v4_workspaces(tenant_id="tenant-b")
        ] == ["workspace-a"]
    finally:
        await engine.close()
