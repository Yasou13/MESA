"""Document lifecycle privacy through the combined runtime and HTTP API."""

import pytest

from mesa_memory.api import server
from tests.test_model_optional_runtime import _consume, model_runtime  # noqa: F401


async def _grant_lifecycle_permissions():
    access = server.state.access_control
    await access.grant_scope_role(
        "model-principal",
        tenant_id="model-tenant",
        workspace_id="default",
        dataset_id="main",
        role="OWNER",
    )
    for permission in ("PURGE", "ROLLBACK"):
        await access.grant_dataset_permission(
            "model-principal",
            tenant_id="model-tenant",
            dataset_id="main",
            permission=permission,
        )


async def _insert_document(env, document="privacy", revision=1, content=None):
    payload = {
        "session_id": env.session_id,
        "dataset_id": "main",
        "document_id": document,
        "revision_id": f"{document}-r{revision}",
        "revision_number": revision,
        "chunk_id": f"{document}-c{revision}",
        "title": "Privacy lifecycle",
        "source_ref": "privacy-test",
        "content": content or f"PRIVATE_REVISION_{revision} durable memory.",
    }
    if revision > 1:
        payload["supersedes_revision_id"] = f"{document}-r{revision - 1}"
    response = await env.client.post("/v4/memory/insert", json=payload)
    assert response.status_code == 202, response.text
    return response.json()["mutation_id"]


async def _context(env):
    response = await env.client.get(f"/v4/sessions/{env.session_id}/context")
    assert response.status_code == 200, response.text
    return response.json()["context"]


@pytest.mark.asyncio
@pytest.mark.parametrize("embedding", [False, True])
@pytest.mark.parametrize("action", ["purge", "rollback", "supersede"])
async def test_session_context_drops_removed_revision(model_runtime, action, embedding):
    async with model_runtime(MESA_EMBEDDING_ENABLED=embedding) as env:
        await _grant_lifecycle_permissions()
        mutation_id = await _insert_document(env)
        # Pending evidence must remain available in the originating session.
        assert "PRIVATE_REVISION_1" in await _context(env)
        await _consume(env, 3)
        assert (await env.dao.get_mutation("model-agent", mutation_id))[
            "state"
        ] == "COMMITTED"
        assert "PRIVATE_REVISION_1" in await _context(env)
        if action == "purge":
            response = await env.client.delete(
                "/v4/catalog/documents/privacy",
                params={
                    "tenant_id": "model-tenant",
                    "workspace_id": "default",
                    "dataset_id": "main",
                },
            )
            assert response.status_code == 202, response.text
        elif action == "rollback":
            response = await env.client.post(f"/v4/mutations/{mutation_id}/rollback")
            assert response.status_code == 202, response.text
        else:
            await _insert_document(env, revision=2)
        await _consume(env, 12)
        context = await _context(env)
        assert "PRIVATE_REVISION_1" not in context
        if action == "supersede":
            assert "PRIVATE_REVISION_2" in context
        await _insert_document(
            env, document="pending", content="PENDING_TEXT stays visible."
        )
        assert "PENDING_TEXT" in await _context(env)
        await env.dao.insert_raw_log(
            "model-agent",
            {"session_id": env.session_id, "content": "UNLINKED_TEXT stays visible."},
        )
        assert "UNLINKED_TEXT" in await _context(env)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["REJECTED", "CANCELLED"])
async def test_session_context_drops_terminal_mutation(model_runtime, state):
    async with model_runtime() as env:
        mutation_id = await _insert_document(env)
        assert "PRIVATE_REVISION_1" in await _context(env)
        if state == "CANCELLED":
            async with env.dao._sql.transaction() as db:
                assert await env.dao._transition_memory_mutation_in_tx(
                    db, mutation_id, to_state=state
                )
                await db.commit()
        else:
            assert await env.dao.set_mutation_state("model-agent", mutation_id, state)
        assert "PRIVATE_REVISION_1" not in await _context(env)
