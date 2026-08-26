"""Sol's mutation-killing adversarial gates for Round 7 certification."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mesa_api.v4_router import create_v4_router
from mesa_client.client import AsyncMesaV4Client
from mesa_mcp.v4_service import MesaHttpV4Service
from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


def _app(dao: MemoryDAO, access_control: AccessControl) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def attach_principal(request: Any, call_next: Any) -> Any:
        principal_id = request.headers.get("X-MESA-Principal")
        if principal_id:
            request.state.principal = SimpleNamespace(
                principal_id=principal_id,
                status="active",
            )
        return await call_next(request)

    app.include_router(
        create_v4_router(lambda: dao, get_access_control=lambda: access_control)
    )
    return app


def _client(dao: MemoryDAO, access_control: AccessControl) -> TestClient:
    return TestClient(_app(dao, access_control), raise_server_exceptions=False)


async def _physical_id(
    dao: MemoryDAO, *, tenant_id: str, kind: str, external_id: str
) -> str:
    async with dao._sql.connection() as db:
        return await dao.catalog.resolve_id_in_tx(
            db,
            tenant_id=tenant_id,
            kind=kind,
            external_id=external_id,
        )


async def _insert_committed_child(
    dao: MemoryDAO,
    *,
    tenant_id: str,
    revision_id: str,
    chunk_id: str,
    suffix: str,
) -> None:
    physical_revision = await _physical_id(
        dao, tenant_id=tenant_id, kind="revision", external_id=revision_id
    )
    physical_chunk = await _physical_id(
        dao, tenant_id=tenant_id, kind="chunk", external_id=chunk_id
    )
    async with dao._sql.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs "
            "(pipeline_run_id, tenant_id, session_id, agent_id, state) "
            "VALUES (?, ?, 'historical-session', 'agent-sol', 'COMMITTED')",
            (f"pipeline-{suffix}", tenant_id),
        )
        await db.execute(
            "INSERT INTO memory_mutations "
            "(mutation_id, candidate_id, tenant_id, agent_id, session_id, "
            "content_payload, pipeline_run_id, revision_id, chunk_id, state) "
            "VALUES (?, ?, ?, 'agent-sol', 'historical-session', 'payload', "
            "?, ?, ?, 'COMMITTED')",
            (
                f"mutation-{suffix}",
                f"candidate-{suffix}",
                tenant_id,
                f"pipeline-{suffix}",
                physical_revision,
                physical_chunk,
            ),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_sol_supported_late_finalize_and_competing_head_are_atomic(
    tmp_path: Any,
) -> None:
    engine = AsyncEngine(str(tmp_path / "late-finalize.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    tenant_id = "tenant-sol-life"
    dataset_id = "dataset-sol-life"
    document_id = "document-sol-life"
    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id,
        workspace_id="workspace-sol-life",
        dataset_id=dataset_id,
    )
    await dao.create_v4_document(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=document_id,
        title="Sol lifecycle",
    )

    async def create_chunk(
        revision_id: str,
        chunk_id: str,
        *,
        finalize: bool,
        supersedes: str | None = None,
        revision_number: int,
    ) -> dict[str, Any]:
        return await dao.create_v4_source_chunk(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=document_id,
            revision_id=revision_id,
            chunk_id=chunk_id,
            title="Sol lifecycle",
            content_payload=f"payload-{revision_id}",
            source_ref=f"source-{revision_id}",
            revision_number=revision_number,
            supersedes_revision_id=supersedes,
            finalize_revision=finalize,
        )

    await create_chunk("rev-one", "chunk-one", finalize=False, revision_number=1)
    await _insert_committed_child(
        dao,
        tenant_id=tenant_id,
        revision_id="rev-one",
        chunk_id="chunk-one",
        suffix="one",
    )
    # Supported public DAO entrypoint performs the only late-finalize event.
    first = await create_chunk("rev-one", "chunk-one", finalize=True, revision_number=1)
    repeated = await create_chunk(
        "rev-one", "chunk-one", finalize=True, revision_number=1
    )
    assert first["manifest_hash"] == repeated["manifest_hash"]

    await create_chunk(
        "rev-two",
        "chunk-two",
        finalize=False,
        supersedes="rev-one",
        revision_number=2,
    )
    await _insert_committed_child(
        dao,
        tenant_id=tenant_id,
        revision_id="rev-two",
        chunk_id="chunk-two",
        suffix="two",
    )
    await create_chunk(
        "rev-two",
        "chunk-two",
        finalize=True,
        supersedes="rev-one",
        revision_number=2,
    )

    # A competing successor with no predecessor claim cannot become a second head;
    # the failed finalization transaction must leave its manifest unfrozen.
    await create_chunk("rev-three", "chunk-three", finalize=False, revision_number=3)
    await _insert_committed_child(
        dao,
        tenant_id=tenant_id,
        revision_id="rev-three",
        chunk_id="chunk-three",
        suffix="three",
    )
    with pytest.raises(ValueError, match="revision-head conflict"):
        await create_chunk("rev-three", "chunk-three", finalize=True, revision_number=3)

    revisions = await dao.list_v4_revisions(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=document_id,
    )
    by_id = {item["revision_id"]: item for item in revisions}
    assert by_id["rev-one"]["status"] == "SUPERSEDED"
    assert by_id["rev-two"]["status"] == "ACTIVE"
    assert by_id["rev-three"]["status"] == "PENDING"
    assert sum(item["status"] == "ACTIVE" for item in revisions) == 1
    physical_three = await _physical_id(
        dao, tenant_id=tenant_id, kind="revision", external_id="rev-three"
    )
    async with engine.connection() as db:
        row = await (
            await db.execute(
                "SELECT manifest_frozen_at FROM document_revisions "
                "WHERE revision_id = ?",
                (physical_three,),
            )
        ).fetchone()
    assert row[0] is None
    await engine.close()


@pytest.mark.asyncio
async def test_sol_historical_replay_binds_mutation_to_session_scope(
    tmp_path: Any,
) -> None:
    engine = AsyncEngine(str(tmp_path / "historical-scope.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    access = AccessControl(str(tmp_path / "historical-rbac.sqlite"))
    await access.initialize()
    client = _client(dao, access)

    tenant_id = "tenant-sol-auth"
    workspace_id = "workspace-sol-auth"
    dataset_id = "dataset-sol-auth"
    other_dataset_id = "dataset-sol-other"
    agent_id = "agent-sol-auth"
    principal_id = "principal-sol-auth"
    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        dataset_id=dataset_id,
    )
    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        dataset_id=other_dataset_id,
    )
    session = await dao.create_v4_session(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        dataset_ids=[dataset_id],
        agent_id=agent_id,
        principal_id=principal_id,
    )
    session_id = str(session["session_id"])
    await access.grant_principal_session_access(
        principal_id=principal_id,
        agent_id=agent_id,
        session_id=session_id,
        level="ADMIN",
    )
    await access.grant_scope_role(
        principal_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        role="OWNER",
    )
    await access.grant_dataset_permission(
        principal_id,
        tenant_id=tenant_id,
        dataset_id=other_dataset_id,
        permission="ROLLBACK",
    )
    await dao.end_v4_session(session_id)

    physical_other = await _physical_id(
        dao,
        tenant_id=tenant_id,
        kind="dataset",
        external_id=other_dataset_id,
    )
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs "
            "(pipeline_run_id, tenant_id, session_id, agent_id, state) "
            "VALUES ('pipeline-scope-attack', ?, ?, ?, 'DLQ')",
            (tenant_id, session_id, agent_id),
        )
        await db.execute(
            "INSERT INTO memory_mutations "
            "(mutation_id, candidate_id, tenant_id, dataset_id, agent_id, "
            "session_id, content_payload, pipeline_run_id, state) "
            "VALUES ('mutation-scope-attack', 'candidate-scope-attack', ?, ?, ?, "
            "?, 'payload', 'pipeline-scope-attack', 'DEAD_LETTER')",
            (tenant_id, physical_other, agent_id, session_id),
        )
        await db.execute(
            "INSERT INTO projection_outbox "
            "(projection_id, mutation_id, projection_name, state) "
            "VALUES ('projection-scope-attack', 'mutation-scope-attack', "
            "'VECTOR', 'DEAD_LETTER')"
        )
        await db.commit()

    response = client.post(
        "/v4/mutations/mutation-scope-attack/replay",
        headers={"X-MESA-Principal": principal_id},
    )
    assert response.status_code == 403
    async with engine.connection() as db:
        states = await (
            await db.execute(
                "SELECT p.state, m.state, o.state FROM pipeline_runs p "
                "JOIN memory_mutations m ON m.pipeline_run_id = p.pipeline_run_id "
                "JOIN projection_outbox o ON o.mutation_id = m.mutation_id "
                "WHERE p.pipeline_run_id = 'pipeline-scope-attack'"
            )
        ).fetchone()
    assert tuple(states) == ("DLQ", "DEAD_LETTER", "DEAD_LETTER")

    # The session's tenant is also immutable historical evidence.
    other_tenant = "tenant-sol-auth-other"
    await dao.ensure_v4_catalog_scope(
        tenant_id=other_tenant,
        workspace_id="workspace-sol-auth-other",
        dataset_id="dataset-sol-auth-other",
    )
    physical_other_tenant_dataset = await _physical_id(
        dao,
        tenant_id=other_tenant,
        kind="dataset",
        external_id="dataset-sol-auth-other",
    )
    await access.grant_dataset_permission(
        principal_id,
        tenant_id=other_tenant,
        dataset_id="dataset-sol-auth-other",
        permission="ROLLBACK",
    )
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs "
            "(pipeline_run_id, tenant_id, session_id, agent_id, state) "
            "VALUES ('pipeline-tenant-attack', ?, ?, ?, 'DLQ')",
            (other_tenant, session_id, agent_id),
        )
        await db.execute(
            "INSERT INTO memory_mutations "
            "(mutation_id, candidate_id, tenant_id, dataset_id, agent_id, "
            "session_id, content_payload, pipeline_run_id, state) "
            "VALUES ('mutation-tenant-attack', 'candidate-tenant-attack', ?, ?, ?, "
            "?, 'payload', 'pipeline-tenant-attack', 'DEAD_LETTER')",
            (
                other_tenant,
                physical_other_tenant_dataset,
                agent_id,
                session_id,
            ),
        )
        await db.commit()
    wrong_tenant = client.post(
        "/v4/mutations/mutation-tenant-attack/replay",
        headers={"X-MESA-Principal": principal_id},
    )
    assert wrong_tenant.status_code == 403

    # A forged mutation workspace cannot borrow the session's dataset grant.
    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id,
        workspace_id="workspace-sol-auth-other",
        dataset_id="dataset-sol-auth-workspace-other",
    )
    physical_other_workspace = await _physical_id(
        dao,
        tenant_id=tenant_id,
        kind="workspace",
        external_id="workspace-sol-auth-other",
    )
    physical_dataset = await _physical_id(
        dao,
        tenant_id=tenant_id,
        kind="dataset",
        external_id=dataset_id,
    )
    await access.grant_dataset_permission(
        principal_id,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        permission="ROLLBACK",
    )
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs "
            "(pipeline_run_id, tenant_id, session_id, agent_id, state) "
            "VALUES ('pipeline-workspace-attack', ?, ?, ?, 'DLQ')",
            (tenant_id, session_id, agent_id),
        )
        await db.execute(
            "INSERT INTO memory_mutations "
            "(mutation_id, candidate_id, tenant_id, workspace_id, dataset_id, "
            "agent_id, session_id, content_payload, pipeline_run_id, state) "
            "VALUES ('mutation-workspace-attack', 'candidate-workspace-attack', "
            "?, ?, ?, ?, ?, 'payload', 'pipeline-workspace-attack', 'DEAD_LETTER')",
            (
                tenant_id,
                physical_other_workspace,
                physical_dataset,
                agent_id,
                session_id,
            ),
        )
        await db.commit()
    wrong_workspace = client.post(
        "/v4/mutations/mutation-workspace-attack/replay",
        headers={"X-MESA-Principal": principal_id},
    )
    assert wrong_workspace.status_code == 403

    wrong_principal = client.post(
        "/v4/mutations/mutation-workspace-attack/replay",
        headers={"X-MESA-Principal": "unbound-principal"},
    )
    assert wrong_principal.status_code == 404
    assert wrong_principal.json()["detail"] == "Unknown session"
    await access.close()
    await engine.close()


@pytest.mark.asyncio
async def test_sol_closed_session_mixed_technical_and_cleanup_replay(
    tmp_path: Any,
) -> None:
    engine = AsyncEngine(str(tmp_path / "historical-technical.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    access = AccessControl(str(tmp_path / "historical-technical-rbac.sqlite"))
    await access.initialize()
    client = _client(dao, access)
    tenant_id = "tenant-sol-technical"
    workspace_id = "workspace-sol-technical"
    dataset_id = "dataset-sol-technical"
    agent_id = "agent-sol-technical"
    principal_id = "principal-sol-technical"
    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        dataset_id=dataset_id,
    )
    session = await dao.create_v4_session(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        dataset_ids=[dataset_id],
        agent_id=agent_id,
        principal_id=principal_id,
    )
    session_id = str(session["session_id"])
    await access.grant_principal_session_access(
        principal_id=principal_id,
        agent_id=agent_id,
        session_id=session_id,
        level="ADMIN",
    )
    await access.grant_scope_role(
        principal_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        role="OWNER",
    )
    await access.grant_dataset_permission(
        principal_id,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        permission="ROLLBACK",
    )
    await dao.end_v4_session(session_id)
    physical_dataset = await _physical_id(
        dao,
        tenant_id=tenant_id,
        kind="dataset",
        external_id=dataset_id,
    )

    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs "
            "(pipeline_run_id, tenant_id, dataset_id, session_id, agent_id, state) "
            "VALUES ('pipeline-mixed', ?, ?, ?, ?, 'DLQ')",
            (tenant_id, physical_dataset, session_id, agent_id),
        )
        for mutation_id, state in (
            ("mutation-rejected", "REJECTED"),
            ("mutation-technical", "DEAD_LETTER"),
        ):
            await db.execute(
                "INSERT INTO memory_mutations "
                "(mutation_id, candidate_id, tenant_id, dataset_id, agent_id, "
                "session_id, content_payload, pipeline_run_id, state) "
                "VALUES (?, ?, ?, ?, ?, ?, 'payload', 'pipeline-mixed', ?)",
                (
                    mutation_id,
                    f"candidate-{mutation_id}",
                    tenant_id,
                    physical_dataset,
                    agent_id,
                    session_id,
                    state,
                ),
            )
        await db.execute(
            "INSERT INTO projection_outbox "
            "(projection_id, mutation_id, projection_name, state) "
            "VALUES ('projection-mixed', 'mutation-technical', 'VECTOR', 'DEAD_LETTER')"
        )
        await db.commit()

    rejected = client.post(
        "/v4/mutations/mutation-rejected/replay",
        headers={"X-MESA-Principal": principal_id},
    )
    assert rejected.status_code == 409
    technical = client.post(
        "/v4/mutations/mutation-technical/replay",
        headers={"X-MESA-Principal": principal_id},
    )
    assert technical.status_code == 202
    # The rejected sibling keeps the aggregate DLQ truthful while only the
    # genuine technical child/outbox becomes retryable.
    assert technical.json()["state"] == "DLQ"
    async with engine.connection() as db:
        mixed = await (
            await db.execute(
                "SELECT p.state, rejected.state, technical.state, o.state "
                "FROM pipeline_runs p "
                "JOIN memory_mutations rejected ON rejected.pipeline_run_id = p.pipeline_run_id "
                "AND rejected.mutation_id = 'mutation-rejected' "
                "JOIN memory_mutations technical ON technical.pipeline_run_id = p.pipeline_run_id "
                "AND technical.mutation_id = 'mutation-technical' "
                "JOIN projection_outbox o ON o.mutation_id = technical.mutation_id "
                "WHERE p.pipeline_run_id = 'pipeline-mixed'"
            )
        ).fetchone()
    assert tuple(mixed) == ("DLQ", "REJECTED", "RETRY_PENDING", "RETRY_PENDING")

    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs "
            "(pipeline_run_id, tenant_id, dataset_id, session_id, agent_id, state) "
            "VALUES ('pipeline-cleanup', ?, ?, ?, ?, 'BLOCKED')",
            (tenant_id, physical_dataset, session_id, agent_id),
        )
        await db.execute(
            "INSERT INTO memory_mutations "
            "(mutation_id, candidate_id, tenant_id, dataset_id, agent_id, "
            "session_id, content_payload, pipeline_run_id, state) "
            "VALUES ('mutation-cleanup', 'candidate-cleanup', ?, ?, ?, ?, "
            "'payload', 'pipeline-cleanup', 'BLOCKED')",
            (tenant_id, physical_dataset, agent_id, session_id),
        )
        await db.execute(
            "INSERT INTO artifact_registry "
            "(registry_id, tenant_id, agent_id, dataset_id, store_name, "
            "artifact_kind, physical_artifact_id, state) "
            "VALUES ('registry-cleanup', ?, ?, ?, 'VECTOR', 'ENTITY_VECTOR', "
            "'artifact-cleanup', 'TOMBSTONED')",
            (tenant_id, agent_id, physical_dataset),
        )
        await db.execute(
            "INSERT INTO artifact_cleanup_outbox "
            "(cleanup_id, pipeline_run_id, registry_id, state) "
            "VALUES ('cleanup-sol', 'pipeline-cleanup', 'registry-cleanup', 'BLOCKED')"
        )
        await db.commit()
    cleanup = client.post(
        "/v4/mutations/mutation-cleanup/replay",
        headers={"X-MESA-Principal": principal_id},
    )
    assert cleanup.status_code == 202
    assert cleanup.json()["state"] == "ROLLING_BACK"
    async with engine.connection() as db:
        cleanup_states = await (
            await db.execute(
                "SELECT p.state, m.state, c.state FROM pipeline_runs p "
                "JOIN memory_mutations m ON m.pipeline_run_id = p.pipeline_run_id "
                "JOIN artifact_cleanup_outbox c ON c.pipeline_run_id = p.pipeline_run_id "
                "WHERE p.pipeline_run_id = 'pipeline-cleanup'"
            )
        ).fetchone()
    assert tuple(cleanup_states) == (
        "ROLLING_BACK",
        "ROLLING_BACK",
        "RETRY_PENDING",
    )
    await access.close()
    await engine.close()


@pytest.mark.asyncio
async def test_sol_public_search_and_status_do_not_leak_catalog_physical_ids(
    tmp_path: Any,
) -> None:
    engine = AsyncEngine(str(tmp_path / "public-leak.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    vector = SimpleNamespace(
        compute_query_embedding=AsyncMock(return_value=[1.0, 0.0]),
        search=AsyncMock(return_value=[]),
    )
    graph = SimpleNamespace(
        insert_node=AsyncMock(),
        insert_assertion=AsyncMock(),
        link_assertions=AsyncMock(),
    )
    dao = MemoryDAO(engine, vector, graph)
    access = AccessControl(str(tmp_path / "public-leak-rbac.sqlite"))
    await access.initialize()
    await dao.ensure_v4_catalog_scope(
        tenant_id="tenant-sol-leak",
        workspace_id="workspace-public",
        dataset_id="dataset-public",
    )
    await dao.create_v4_session(
        tenant_id="tenant-sol-leak",
        workspace_id="workspace-public",
        dataset_ids=["dataset-public"],
        agent_id="agent-sol-leak",
        principal_id="principal-sol-leak",
        session_id="session-sol-leak",
    )
    await access.grant_principal_session_access(
        principal_id="principal-sol-leak",
        agent_id="agent-sol-leak",
        session_id="session-sol-leak",
        level="ADMIN",
    )
    await access.grant_access(
        agent_id="agent-sol-leak",
        session_id="session-sol-leak",
        level="ADMIN",
    )
    await access.grant_scope_role(
        "principal-sol-leak",
        tenant_id="tenant-sol-leak",
        workspace_id="workspace-public",
        dataset_id="dataset-public",
        role="OWNER",
    )
    candidate = MemoryCandidate.from_raw_log(
        raw_log_id=701,
        tenant_id="tenant-sol-leak",
        workspace_id="workspace-public",
        dataset_id="dataset-public",
        document_id="document-public",
        revision_id="revision-public",
        chunk_id="chunk-public",
        source_ref="source-public",
        agent_id="agent-sol-leak",
        session_id="session-sol-leak",
        content_payload="Sol Public Court",
        embedding_provider="test",
        embedding_model="sol",
        embedding_version="v1",
        embedding_dimension=2,
        embedding_space_id="test:sol:v1:2:norm=true",
        embedding_normalized=True,
    ).as_consolidation_record()
    await dao.record_mutation(candidate, raw_log_id=701)
    mutation = await dao.get_projection_mutation(str(candidate["mutation_id"]))
    assert mutation is not None
    await dao.project_v4_sql_entity(mutation=mutation, entity_name="Sol Public Court")
    await dao.project_v4_graph_triplet(
        mutation=mutation,
        triplet={
            "head": "Sol Public Court",
            "relation": "SCOPE",
            "literal_value": "public",
        },
    )
    async with engine.transaction() as db:
        await db.execute(
            "UPDATE memory_mutations SET state = 'COMMITTED' WHERE mutation_id = ?",
            (candidate["mutation_id"],),
        )
        await db.commit()

    private_ids = {
        await _physical_id(
            dao,
            tenant_id="tenant-sol-leak",
            kind=kind,
            external_id=external_id,
        )
        for kind, external_id in (
            ("workspace", "workspace-public"),
            ("dataset", "dataset-public"),
            ("document", "document-public"),
            ("revision", "revision-public"),
            ("chunk", "chunk-public"),
        )
    }
    results = await dao.search_v4_memory(
        tenant_id="tenant-sol-leak",
        agent_id="agent-sol-leak",
        dataset_ids=["dataset-public"],
        query="Sol Public Court",
    )
    assert results
    provenance = results[0]["provenance"][0]
    assert provenance["dataset_id"] == "dataset-public"
    assert provenance["document_id"] == "document-public"
    assert provenance["revision_id"] == "revision-public"
    assert provenance["chunk_id"] == "chunk-public"
    summary = await dao.get_mutation_summary(str(candidate["mutation_id"]))
    assert summary is not None
    app = _app(dao, access)
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"X-MESA-Principal": "principal-sol-leak"}
    api_search = client.post(
        "/v4/memory/search",
        headers=headers,
        json={
            "session_id": "session-sol-leak",
            "dataset_ids": ["dataset-public"],
            "query": "Sol Public Court",
            "limit": 10,
        },
    )
    assert api_search.status_code == 200
    api_status = client.get(
        f"/v4/mutations/{candidate['mutation_id']}", headers=headers
    )
    assert api_status.status_code == 200
    api_context = client.get(
        "/v4/sessions/session-sol-leak/context",
        headers=headers,
        params={"query": "Sol Public Court", "token_budget": 512},
    )
    assert api_context.status_code == 200

    sdk = AsyncMesaV4Client(base_url="http://sol.test", max_retries=0)
    await sdk._client.aclose()
    sdk._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://sol.test",
        headers=headers,
    )
    sdk_status = await sdk.status(str(candidate["mutation_id"]))
    mcp = object.__new__(MesaHttpV4Service)
    mcp._http_client = sdk
    mcp_status = await mcp.v4_mutation_status(str(candidate["mutation_id"]))

    serialized_public = repr(
        {
            "results": results,
            "summary": summary,
            "api_search": api_search.json(),
            "api_status": api_status.json(),
            "api_context": api_context.json(),
            "sdk": sdk_status,
            "mcp": mcp_status,
        }
    )
    assert all(private_id not in serialized_public for private_id in private_ids)
    assert all("metadata_json" not in artifact for artifact in summary["artifacts"])
    await sdk.aclose()
    await access.close()
    await engine.close()


@pytest.mark.asyncio
async def test_sol_reconciliation_repair_persists_physical_dataset_scope(
    tmp_path: Any,
) -> None:
    engine = AsyncEngine(str(tmp_path / "reconciliation.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    vector = SimpleNamespace(get_active_node_ids=AsyncMock(return_value=[]))
    dao = MemoryDAO(engine, vector)
    tenant_id = "tenant-sol-reconcile"
    dataset_id = "dataset-public-reconcile"
    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id,
        workspace_id="workspace-public-reconcile",
        dataset_id=dataset_id,
    )
    await dao.resolve_v4_entity(
        tenant_id=tenant_id,
        canonical_name="Unregistered entity",
    )
    physical_dataset = await _physical_id(
        dao,
        tenant_id=tenant_id,
        kind="dataset",
        external_id=dataset_id,
    )
    result = await dao.reconcile_v4_bidirectional(
        tenant_id=tenant_id,
        agent_id="agent-sol-reconcile",
        dataset_ids=[dataset_id],
        repair=True,
    )
    assert result["cleanup_enqueued"] == 1
    async with engine.connection() as db:
        pipeline_dataset = await (
            await db.execute(
                "SELECT dataset_id FROM pipeline_runs "
                "WHERE session_id = 'reconciliation'"
            )
        ).fetchone()
        registry_dataset = await (
            await db.execute(
                "SELECT dataset_id FROM artifact_registry " "WHERE state = 'TOMBSTONED'"
            )
        ).fetchone()
    assert pipeline_dataset[0] == physical_dataset
    assert registry_dataset[0] == physical_dataset
    assert physical_dataset != dataset_id
    await engine.close()


@pytest.mark.asyncio
async def test_sol_multichunk_hashes_and_catalog_ids_remain_separate(
    tmp_path: Any,
) -> None:
    engine = AsyncEngine(str(tmp_path / "hash-catalog.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    tenant_id = "tenant-sol-hash"
    dataset_id = "shared-public-id"
    document_id = "document-sol-hash"
    revision_id = "revision-sol-hash"
    declared = "a" * 64
    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id,
        workspace_id="workspace-sol-hash",
        dataset_id=dataset_id,
    )
    await dao.create_v4_document(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=document_id,
        title="Sol hashes",
    )
    await dao.create_v4_revision(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=document_id,
        revision_id=revision_id,
        revision_number=1,
        content_hash=declared,
    )
    first = await dao.create_v4_source_chunk(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=document_id,
        revision_id=revision_id,
        chunk_id="chunk-a",
        title="Sol hashes",
        content_payload="chunk payload A",
        source_ref="source-a",
        chunk_ordinal=0,
        finalize_revision=False,
    )
    second = await dao.create_v4_source_chunk(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=document_id,
        revision_id=revision_id,
        chunk_id="chunk-b",
        title="Sol hashes",
        content_payload="chunk payload B",
        source_ref="source-b",
        chunk_ordinal=1,
        finalize_revision=True,
    )
    revisions = await dao.list_v4_revisions(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=document_id,
    )
    assert revisions[0]["declared_content_hash"] == declared
    assert first["content_hash"] == hashlib.sha256(b"chunk payload A").hexdigest()
    assert second["content_hash"] == hashlib.sha256(b"chunk payload B").hexdigest()
    assert first["content_hash"] != second["content_hash"]
    assert second["manifest_hash"] not in {
        declared,
        first["content_hash"],
        second["content_hash"],
    }

    await dao.ensure_v4_catalog_scope(
        tenant_id="tenant-sol-hash-two",
        workspace_id="workspace-sol-hash",
        dataset_id=dataset_id,
    )
    async with engine.connection() as db:
        rows = await (
            await db.execute(
                "SELECT tenant_id, kind, external_id, physical_id "
                "FROM v4_catalog_identities ORDER BY tenant_id, kind, external_id"
            )
        ).fetchall()
    assert rows
    assert all(row[2] != row[3] for row in rows)
    assert len({row[3] for row in rows}) == len(rows)
    shared = [row for row in rows if row[1] == "dataset" and row[2] == dataset_id]
    assert len(shared) == 2
    assert shared[0][3] != shared[1][3]
    await engine.close()
