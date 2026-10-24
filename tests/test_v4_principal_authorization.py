"""V4 equivalents of the V3 principal authorization release contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Request

from mesa_memory.security.rbac import AccessControl
from tests.test_v4_api_contract import _access, _app
from tests.test_v4_api_contract import asgi_client as asgi_client


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,level", [("insert", "WRITE"), ("search", "READ")])
async def test_v4_memory_requires_principal_session_access(
    asgi_client, operation, level
):
    dao = MagicMock()
    dao.get_v4_session = AsyncMock(
        return_value={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "dataset_ids": ["dataset-a"],
            "agent_id": "agent-a",
            "session_id": "session-a",
            "status": "ACTIVE",
        }
    )
    dao.admit_v4_memory = AsyncMock()
    dao.search_v4_memory = AsyncMock()
    access = _access()
    access.check_principal_session_access.return_value = False
    payload = (
        {"session_id": "session-a", "query": "x"}
        if operation == "search"
        else {
            "session_id": "session-a",
            "dataset_id": "dataset-a",
            "document_id": "document-a",
            "revision_id": "revision-a",
            "chunk_id": "chunk-a",
            "title": "Document",
            "source_ref": "source-a",
            "content": "x",
        }
    )
    response = await asgi_client(_app(dao, access)).post(
        f"/v4/memory/{operation}",
        json=payload,
    )
    assert response.status_code == 403
    access.check_principal_session_access.assert_awaited_once_with(
        "principal-a",
        "agent-a",
        "session-a",
        level,
    )
    dao.admit_v4_memory.assert_not_awaited()
    dao.search_v4_memory.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="V4 gap: mutation status returns 403 rather than concealing a revoked session grant with 404",
)
async def test_v4_status_conceals_records_without_session_read_access(asgi_client):
    dao = MagicMock()
    dao.get_mutation_summary = AsyncMock(return_value={"session_id": "session-a"})
    dao.get_v4_session = AsyncMock(return_value={"agent_id": "agent-a"})
    dao.get_pipeline_run = AsyncMock()
    access = _access()
    access.check_principal_session_access.return_value = False
    response = await asgi_client(_app(dao, access)).get("/v4/mutations/mutation-a")
    access.check_principal_session_access.assert_awaited_once_with(
        "principal-a",
        "agent-a",
        "session-a",
        "READ",
    )
    dao.get_pipeline_run.assert_not_awaited()
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("mapping", [None, "READ"])
async def test_v4_session_start_denies_unmapped_or_read_only_principal_without_self_grant(
    tmp_path,
    asgi_client,
    mapping,
):
    access = AccessControl(policy_path=str(tmp_path / "rbac.sqlite"))
    await access.initialize()
    dao = MagicMock()
    dao.create_v4_session = AsyncMock()
    try:
        if mapping:
            await access.grant_principal_permission("principal-a", "agent-a", mapping)
        access.grant_access = AsyncMock()
        access.grant_principal_session_access = AsyncMock()
        response = await asgi_client(_app(dao, access)).post(
            "/v4/sessions/start",
            json={
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "dataset_ids": ["dataset-a"],
                "agent_id": "agent-a",
            },
        )
        assert response.status_code == 403
        dao.create_v4_session.assert_not_awaited()
        access.grant_access.assert_not_awaited()
        access.grant_principal_session_access.assert_not_awaited()
    finally:
        await access.close()


@pytest.mark.asyncio
async def test_v4_session_start_rejects_inactive_principal(asgi_client):
    dao = MagicMock()
    access = _access()
    app = _app(dao, access)

    # Replace the existing principal dependency; leave the real V4 handler intact.
    attach_principal = app.router.dependencies[0].dependency

    async def inactive(request: Request):
        request.state.principal = SimpleNamespace(
            principal_id="principal-a",
            principal_type="USER",
            status="disabled",
        )

    app.dependency_overrides[attach_principal] = inactive
    response = await asgi_client(app).post(
        "/v4/sessions/start",
        json={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "dataset_ids": ["dataset-a"],
            "agent_id": "agent-a",
        },
    )
    assert response.status_code == 401
    access.check_principal_permission.assert_not_awaited()
    access.grant_access.assert_not_awaited()
