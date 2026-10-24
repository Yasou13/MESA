"""Real V4 authentication, durable principal grants, and scope isolation."""

from unittest.mock import AsyncMock

import pytest

from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import MemoryDAO
from tests.v4_release_helpers import insert_memory
from tests.v4_release_helpers import v4_release_env as v4_release_env


@pytest.mark.asyncio
async def test_v4_authenticated_session_routes_bind_principals_server_side(
    v4_release_env, monkeypatch
):
    env = v4_release_env
    context_url = f"/v4/sessions/{env.session_id}/context"
    for headers in ({}, {"X-API-Key": "invalid-key"}):
        request = env.client.build_request("GET", context_url)
        request.headers.pop("X-API-Key", None)
        request.headers.update(headers)
        assert (await env.client.send(request)).status_code == 401

    reopened = AccessControl(policy_path=env.access.policy_path)
    await reopened.initialize()
    try:
        assert await reopened.check_principal_session_access(
            "principal-a",
            "agent-a",
            env.session_id,
            "WRITE",
        )
    finally:
        await reopened.close()

    await env.dao.ensure_v4_catalog_scope(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        dataset_id="dataset-b",
    )
    await env.access.grant_principal_permission(
        "principal-b", "agent-b", "SESSION_CREATE"
    )
    await env.access.grant_scope_role(
        "principal-b",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        dataset_id="dataset-b",
        role="WRITER",
    )
    env.principal("principal-b")
    started_b = await env.client.post(
        "/v4/sessions/start",
        json={
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "dataset_ids": ["dataset-b"],
            "agent_id": "agent-b",
        },
    )
    assert started_b.status_code == 201
    session_b = started_b.json()["session_id"]
    env.principal()

    logs = AsyncMock(return_value=[])
    search = AsyncMock(return_value=[])
    mutations = AsyncMock(return_value=[])
    finalize = AsyncMock()
    purge = AsyncMock()
    monkeypatch.setattr(MemoryDAO, "get_recent_logs", logs)
    monkeypatch.setattr(MemoryDAO, "search_v4_memory", search)
    monkeypatch.setattr(MemoryDAO, "list_session_mutation_summaries", mutations)
    monkeypatch.setattr(MemoryDAO, "request_session_finalization", finalize)
    monkeypatch.setattr(MemoryDAO, "purge_v4_document", purge)

    own = await env.client.get(context_url)
    assert own.status_code == 200, own.text
    # V4 obtains agent_id from storage. A forged query parameter cannot select
    # another agent, even though unknown query parameters are ignored.
    forged = await env.client.get(context_url, params={"agent_id": "agent-b"})
    assert forged.status_code == 200
    assert forged.json()["agent_id"] == "agent-a"
    for call in logs.await_args_list:
        assert call.args[0] == "agent-a"
    logs.reset_mock()
    search.reset_mock()
    mutations.reset_mock()

    foreign = await env.client.get(f"/v4/sessions/{session_b}/context")
    assert foreign.status_code == 404
    assert (await env.client.post(f"/v4/sessions/{session_b}/end")).status_code == 404
    assert (
        await env.client.post(
            "/v4/memory/search",
            json={
                "session_id": env.session_id,
                "dataset_ids": ["dataset-b"],
                "query": "x",
            },
        )
    ).status_code == 403
    assert (
        await env.client.delete(
            "/v4/catalog/documents/document-b",
            params={
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "dataset_id": "dataset-b",
            },
        )
    ).status_code == 403

    env.principal("principal-a", status="inactive")
    assert (await env.client.get(context_url)).status_code == 401
    env.principal("principal-unmapped")
    assert (await env.client.get(context_url)).status_code == 404
    logs.assert_not_awaited()
    search.assert_not_awaited()
    mutations.assert_not_awaited()
    finalize.assert_not_awaited()
    purge.assert_not_awaited()


@pytest.mark.asyncio
async def test_v4_status_conceals_foreign_principal_records(v4_release_env):
    env = v4_release_env
    admitted = await insert_memory(env)
    env.principal("principal-b")
    foreign = await env.client.get(f"/v4/mutations/{admitted['mutation_id']}")
    missing = await env.client.get("/v4/mutations/missing")
    assert foreign.status_code == missing.status_code == 404


@pytest.mark.asyncio
async def test_v4_read_only_principal_cannot_end_session(v4_release_env, monkeypatch):
    env = v4_release_env
    await env.access.grant_principal_session_access(
        "principal-read",
        "agent-a",
        env.session_id,
        "READ",
    )
    await env.access.grant_scope_role(
        "principal-read",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        dataset_id="dataset-a",
        role="READER",
    )
    finalize = AsyncMock()
    monkeypatch.setattr(MemoryDAO, "request_session_finalization", finalize)
    env.principal("principal-read")
    response = await env.client.post(f"/v4/sessions/{env.session_id}/end")
    assert response.status_code in {403, 404}
    finalize.assert_not_awaited()
    assert (await env.dao.get_v4_session(env.session_id))["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_v4_document_purge_requires_owner_and_explicit_permission(
    v4_release_env, monkeypatch
):
    env = v4_release_env
    purge = AsyncMock(
        return_value={"document_id": "document-a", "status": "PURGE_PENDING"}
    )
    monkeypatch.setattr(MemoryDAO, "purge_v4_document", purge)
    params = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "dataset_id": "dataset-a",
    }
    url = "/v4/catalog/documents/document-a"
    assert (await env.client.delete(url, params=params)).status_code == 403
    await env.access.grant_scope_role(
        "principal-a",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        dataset_id="dataset-a",
        role="OWNER",
    )
    assert (await env.client.delete(url, params=params)).status_code == 403
    purge.assert_not_awaited()
    await env.access.grant_dataset_permission(
        "principal-a",
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        permission="PURGE",
    )
    assert (await env.client.delete(url, params=params)).status_code == 202
    purge.assert_awaited_once_with(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        document_id="document-a",
    )
    assert (
        await env.client.delete(url, params={**params, "dataset_id": "dataset-b"})
    ).status_code == 403
    assert purge.await_count == 1


@pytest.mark.asyncio
# Decision D5: session context is readable only by its creator; non-creator gets 404.
async def test_v4_session_context_concealed_with_404_from_non_creator(
    v4_release_env, monkeypatch
):
    env = v4_release_env
    await env.access.grant_principal_session_access(
        "principal-read",
        "agent-a",
        env.session_id,
        "READ",
    )
    await env.access.grant_scope_role(
        "principal-read",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        dataset_id="dataset-a",
        role="READER",
    )
    monkeypatch.setattr(MemoryDAO, "get_recent_logs", AsyncMock(return_value=[]))
    monkeypatch.setattr(MemoryDAO, "search_v4_memory", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        MemoryDAO, "list_session_mutation_summaries", AsyncMock(return_value=[])
    )
    env.principal("principal-read")
    ended = await env.client.post(f"/v4/sessions/{env.session_id}/end")
    assert ended.status_code in {403, 404}
    response = await env.client.get(f"/v4/sessions/{env.session_id}/context")
    assert response.status_code == 404
