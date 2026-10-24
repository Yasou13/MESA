"""Real SQLite/RBAC environment for the V4 release-gate ports."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import Depends, FastAPI

from mesa_api.v4_router import create_v4_router
from mesa_memory.api import server
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.fixture
async def v4_release_env(tmp_path, monkeypatch):
    # The existing V4 API helpers mock storage and authentication. These flow
    # contracts also need durable storage and the real API-key dependency.
    sql = AsyncEngine(str(tmp_path / "v4-release.sqlite"), max_connections=4)
    await sql.initialize()
    await initialize_schema(sql)
    dao = MemoryDAO(sql, SimpleNamespace())
    access = AccessControl(policy_path=str(tmp_path / "rbac.sqlite"))
    await access.initialize()
    monkeypatch.setattr(server.state, "api_key_store", None, raising=False)
    monkeypatch.setattr(server, "_MESA_API_KEY", "v4-release-key")
    monkeypatch.setattr(server, "_MESA_PRINCIPAL_TYPE", "USER")

    def principal(identifier="principal-a", status="active"):
        monkeypatch.setattr(server, "_MESA_PRINCIPAL_ID", identifier)
        monkeypatch.setattr(server, "_MESA_PRINCIPAL_STATUS", status)

    async def get_dao():
        return dao

    async def get_access():
        return access

    app = FastAPI()
    app.include_router(
        create_v4_router(get_dao=get_dao, get_access_control=get_access),
        dependencies=[Depends(server.get_api_key)],
    )
    try:
        await dao.ensure_v4_catalog_scope(
            tenant_id="tenant-a", workspace_id="workspace-a", dataset_id="dataset-a"
        )
        await access.grant_principal_permission(
            "principal-a", "agent-a", "SESSION_CREATE"
        )
        await access.grant_scope_role(
            "principal-a",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            dataset_id="dataset-a",
            role="WRITER",
        )
        principal()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
            headers={"X-API-Key": "v4-release-key"},
        ) as client:
            started = await client.post(
                "/v4/sessions/start",
                json={
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "dataset_ids": ["dataset-a"],
                    "agent_id": "agent-a",
                },
            )
            assert started.status_code == 201, started.text
            yield SimpleNamespace(
                dao=dao,
                sql=sql,
                access=access,
                client=client,
                principal=principal,
                session_id=started.json()["session_id"],
            )
    finally:
        await access.close()
        await sql.close()


async def insert_memory(env, *, idempotency_key="insert-a"):
    response = await env.client.post(
        "/v4/memory/insert",
        json={
            "session_id": env.session_id,
            "dataset_id": "dataset-a",
            "document_id": "document-a",
            "revision_id": "revision-a",
            "chunk_id": "chunk-a",
            "title": "Document A",
            "source_ref": "source-a",
            "content": "The durable V4 payload.",
            "idempotency_key": idempotency_key,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()
