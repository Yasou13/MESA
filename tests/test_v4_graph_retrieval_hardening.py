"""Semantic capability truthfulness, embedding error mapping, and session oracle elimination."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException
from starlette.requests import Request

from mesa_api.v4_router import _authorized_v4_session, create_v4_router
from mesa_memory.embedding.service import (
    EmbeddingGenerationError,
    EmbeddingIdentity,
    EmbeddingService,
    EmbeddingUnavailableError,
)
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import V4_VECTOR_REPRESENTATION_VERSION, MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine


async def _create_test_env(
    tmp_path, *, agent_id: str = "test-agent", tenant_id: str = "test-tenant"
):
    db_file = tmp_path / f"{agent_id}_mesa.db"
    sql = AsyncEngine(str(db_file))
    await sql.initialize()
    await initialize_schema(sql)

    identity = EmbeddingIdentity(
        provider="mock", model="hardening-test", version="v1", dimension=8
    )
    vector = VectorEngine(
        str(tmp_path / f"{agent_id}_vectors.lance"),
        max_workers=1,
        embedding_service=EmbeddingService(identity=identity),
    )
    await vector.initialize()

    dao = MemoryDAO(sqlite_engine=sql, vector_engine=vector)
    await dao.create_v4_workspace(
        tenant_id=tenant_id,
        workspace_id="default-ws",
        workspace_name="Default Workspace",
    )
    await dao.ensure_v4_catalog_scope(
        tenant_id=tenant_id,
        workspace_id="default-ws",
        dataset_id="default-ds",
    )
    return sql, vector, dao


async def _close_test_env(sql, vector):
    if vector is not None:
        await vector.close()
    if sql is not None:
        await sql.close()


async def _ingest_entity_and_assertion(
    dao: MemoryDAO,
    *,
    tenant_id: str,
    agent_id: str,
    dataset_id: str,
    mutation_id: str,
    subject_name: str,
    predicate: str,
    object_name: str,
    confidence: float = 1.0,
):
    """Helper to ingest canonical entities and assertion in SQLite."""
    s_entity = await dao.resolve_v4_entity(
        tenant_id=tenant_id, canonical_name=subject_name
    )
    o_entity = await dao.resolve_v4_entity(
        tenant_id=tenant_id, canonical_name=object_name
    )
    s_id = s_entity["entity_id"]
    o_id = o_entity["entity_id"]

    async with dao._sql.transaction() as db:
        ds_id = await dao._catalog.resolve_id_in_tx(
            db, tenant_id=tenant_id, kind="dataset", external_id=dataset_id
        )

        pipe_id = f"pipe_{mutation_id}"
        await db.execute(
            "INSERT OR IGNORE INTO pipeline_runs (pipeline_run_id, tenant_id, session_id, agent_id, state) "
            "VALUES (?, ?, 'test-sess', ?, 'COMPLETED')",
            (pipe_id, tenant_id, agent_id),
        )

        await db.execute(
            "INSERT OR IGNORE INTO memory_mutations (mutation_id, candidate_id, session_id, agent_id, tenant_id, content_payload, state) "
            "VALUES (?, ?, 'sess_1', ?, ?, 'payload', 'COMMITTED')",
            (mutation_id, f"cand_{mutation_id}", agent_id, tenant_id),
        )

        for eid in (s_id, o_id):
            reg_id = f"reg_{eid}"
            await db.execute(
                "INSERT OR IGNORE INTO artifact_registry (registry_id, tenant_id, agent_id, store_name, artifact_kind, physical_artifact_id, state) "
                "VALUES (?, ?, ?, 'canonical', 'ENTITY', ?, 'ACTIVE')",
                (reg_id, tenant_id, agent_id, eid),
            )
            await db.execute(
                "INSERT OR IGNORE INTO artifact_sources (source_ownership_id, registry_id, mutation_id, dataset_id, state) "
                "VALUES (?, ?, ?, ?, 'ACTIVE')",
                (f"src_{eid}_{mutation_id}", reg_id, mutation_id, ds_id),
            )

        a_id = f"ast_{mutation_id}_{s_id}_{o_id}"
        await db.execute(
            "INSERT OR IGNORE INTO v4_assertions ("
            "assertion_id, tenant_id, dataset_id, subject_id, predicate, object_entity_id, "
            "source_ref, document_id, revision_id, chunk_id, confidence, status, mutation_id, pipeline_run_id"
            ") VALUES (?, ?, ?, ?, ?, ?, '', '', '', '', ?, 'ACTIVE', ?, ?)",
            (
                a_id,
                tenant_id,
                ds_id,
                s_id,
                predicate,
                o_id,
                confidence,
                mutation_id,
                pipe_id,
            ),
        )
        reg_aid = f"reg_vec_{a_id}"
        identity = dao._vec.embedding_identity
        vector_metadata = {
            **identity.as_dict(),
            "embedding_dimension": identity.dimension,
            "representation_version": V4_VECTOR_REPRESENTATION_VERSION,
        }
        await db.execute(
            "INSERT OR IGNORE INTO artifact_registry (registry_id, tenant_id, agent_id, store_name, artifact_kind, physical_artifact_id, state, metadata_json) "
            "VALUES (?, ?, ?, 'canonical', 'ASSERTION_VECTOR', ?, 'ACTIVE', ?)",
            (
                reg_aid,
                tenant_id,
                agent_id,
                a_id,
                json.dumps(vector_metadata),
            ),
        )
        await db.execute(
            "INSERT OR IGNORE INTO artifact_sources (source_ownership_id, registry_id, mutation_id, dataset_id, state) "
            "VALUES (?, ?, ?, ?, 'ACTIVE')",
            (f"src_vec_{a_id}_{mutation_id}", reg_aid, mutation_id, ds_id),
        )
        await db.commit()

    return s_id, o_id, a_id


@pytest.mark.asyncio
async def test_j_semantic_capability_and_failure_handling(tmp_path):
    """Test J: Verify semantic truthfulness on unconfigured backend and 503 error mapping on embedding failures."""
    sql, vector, dao = await _create_test_env(tmp_path)
    try:
        unloaded_service = EmbeddingService(
            identity=EmbeddingIdentity(
                provider="local", model="missing-model", version="v1", dimension=8
            )
        )
        assert unloaded_service.is_operational is False

        vec_unloaded = VectorEngine(
            str(tmp_path / "unloaded.lance"),
            embedding_service=unloaded_service,
        )
        assert vec_unloaded.semantic_operational is False
        assert vec_unloaded.semantic_runtime_available is False

        _subject_id, _object_id, assertion_id = await _ingest_entity_and_assertion(
            dao,
            tenant_id="test-tenant",
            agent_id="test-agent",
            dataset_id="default-ds",
            mutation_id="m1",
            subject_name="Alice",
            predicate="leads",
            object_name="Aurora",
        )
        await vector.upsert(
            assertion_id,
            "test-agent",
            [1.0] + [0.0] * 7,
        )

        await dao.create_v4_session(
            tenant_id="test-tenant",
            workspace_id="default-ws",
            dataset_ids=["default-ds"],
            agent_id="test-agent",
            principal_id="test-principal",
            session_id="test-sess-sem",
        )
        access_control = AccessControl(policy_path=str(tmp_path / "rbac_j.db"))
        await access_control.initialize()
        await access_control.grant_scope_role(
            "test-principal",
            tenant_id="test-tenant",
            workspace_id="default-ws",
            dataset_id="default-ds",
            role="WRITER",
        )
        await access_control.grant_principal_session_access(
            "test-principal", "test-agent", "test-sess-sem", "READ"
        )
        await access_control.grant_access("test-agent", "test-sess-sem", "READ")

        async def attach_principal(request: Request) -> None:
            request.state.principal = SimpleNamespace(
                principal_id="test-principal",
                principal_type="USER",
                status="active",
                roles={"admin": False},
            )

        app = FastAPI(dependencies=[Depends(attach_principal)])
        app.include_router(
            create_v4_router(
                get_dao=lambda: dao,
                get_access_control=lambda: access_control,
            )
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            with patch.object(
                vector,
                "compute_query_embedding",
                side_effect=EmbeddingUnavailableError("embedding backend down"),
            ):
                res = await client.post(
                    "/v4/memory/search",
                    json={"session_id": "test-sess-sem", "query": "Alice"},
                )
                assert res.status_code == 503
                assert res.json()["detail"] == "vector_backend_unavailable"
        await access_control.close()
    finally:
        await _close_test_env(sql, vector)


@pytest.mark.asyncio
async def test_j_custom_semantic_provider_requires_operational_evidence(tmp_path):
    """Configured custom providers are not operational until a query succeeds."""

    async def unavailable_provider(text: str) -> list[float]:
        raise ConnectionError("embedding provider unavailable")

    identity = EmbeddingIdentity(
        provider="custom", model="provider-test", version="v1", dimension=2
    )
    unavailable_service = EmbeddingService(
        identity=identity,
        async_provider_fn=unavailable_provider,
    )
    unavailable_vector = VectorEngine(
        str(tmp_path / "unavailable_semantic.lance"),
        embedding_service=unavailable_service,
    )
    await unavailable_vector.initialize()
    try:
        assert unavailable_service.is_configured is True
        assert unavailable_service.is_operational is False
        assert unavailable_vector.semantic_configured is True
        assert unavailable_vector.semantic_operational is False
        with pytest.raises(EmbeddingGenerationError):
            await unavailable_vector.compute_query_embedding("Alice")
        assert unavailable_vector.semantic_operational is False
    finally:
        await unavailable_vector.close()

    async def available_provider(text: str) -> list[float]:
        return [1.0, 0.0]

    available_service = EmbeddingService(
        identity=identity,
        async_provider_fn=available_provider,
    )
    available_vector = VectorEngine(
        str(tmp_path / "available_semantic.lance"),
        embedding_service=available_service,
    )
    await available_vector.initialize()
    try:
        assert available_vector.semantic_configured is True
        assert available_vector.semantic_operational is False
        assert await available_vector.compute_query_embedding("Alice") == [1.0, 0.0]
        assert available_vector.semantic_operational is True
    finally:
        await available_vector.close()


@pytest.mark.asyncio
async def test_k_session_oracle_elimination(tmp_path):
    """Test K: inaccessible sessions have one externally observable error class."""
    sql = AsyncEngine(str(tmp_path / "session_oracle.db"))
    await sql.initialize()
    await initialize_schema(sql)
    vector = None
    dao = MemoryDAO(sqlite_engine=sql, vector_engine=None)
    await dao.create_v4_workspace(
        tenant_id="test-tenant",
        workspace_id="default-ws",
        workspace_name="Default Workspace",
    )
    await dao.ensure_v4_catalog_scope(
        tenant_id="test-tenant",
        workspace_id="default-ws",
        dataset_id="default-ds",
    )
    try:
        access_control = AccessControl(policy_path=str(tmp_path / "rbac.db"))
        await access_control.initialize()

        active_sess = await dao.create_v4_session(
            tenant_id="test-tenant",
            workspace_id="default-ws",
            dataset_ids=["default-ds"],
            agent_id="test-agent",
            principal_id="owner-principal",
            session_id="sess-active-101",
        )
        ended_sess = await dao.create_v4_session(
            tenant_id="test-tenant",
            workspace_id="default-ws",
            dataset_ids=["default-ds"],
            agent_id="test-agent",
            principal_id="owner-principal",
            session_id="sess-ended-202",
        )
        await dao.end_v4_session("sess-ended-202")

        active_session_id = active_sess["session_id"]
        ended_session_id = ended_sess["session_id"]

        await access_control.grant_scope_role(
            "owner-principal",
            tenant_id="test-tenant",
            workspace_id="default-ws",
            dataset_id="default-ds",
            role="WRITER",
        )
        await access_control.grant_principal_session_access(
            "owner-principal", "test-agent", active_session_id, "WRITE"
        )
        await access_control.grant_principal_session_access(
            "owner-principal", "test-agent", ended_session_id, "WRITE"
        )
        await access_control.grant_access("test-agent", active_session_id, "WRITE")
        await access_control.grant_access("test-agent", ended_session_id, "WRITE")

        def make_request(principal_id: str):
            req = Request(scope={"type": "http", "headers": []})
            req.state.principal = SimpleNamespace(
                principal_id=principal_id,
                status="active",
                roles={"admin": False},
            )
            return req

        async def attach_foreign_principal(request: Request) -> None:
            request.state.principal = SimpleNamespace(
                principal_id="foreign-principal",
                principal_type="USER",
                status="active",
                roles={"admin": False},
            )

        app = FastAPI(dependencies=[Depends(attach_foreign_principal)])
        app.include_router(
            create_v4_router(
                get_dao=lambda: dao,
                get_access_control=lambda: access_control,
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            probe_responses = [
                await client.post(
                    "/v4/memory/search",
                    json={"session_id": probed_session_id, "query": "Alice"},
                )
                for probed_session_id in (
                    "nonexistent-session",
                    active_session_id,
                    ended_session_id,
                )
            ]
        assert [
            (
                response.status_code,
                response.json(),
                response.headers["content-type"],
            )
            for response in probe_responses
        ] == [
            (404, {"detail": "Unknown session"}, "application/json"),
            (404, {"detail": "Unknown session"}, "application/json"),
            (404, {"detail": "Unknown session"}, "application/json"),
        ]

        inaccessible_errors = []
        for probed_session_id in (
            "nonexistent-session",
            active_session_id,
            ended_session_id,
        ):
            with pytest.raises(HTTPException) as exc:
                await _authorized_v4_session(
                    make_request("foreign-principal"),
                    dao,
                    access_control,
                    probed_session_id,
                    level="WRITE",
                )
            inaccessible_errors.append((exc.value.status_code, exc.value.detail))

        assert inaccessible_errors == [
            (404, "Unknown session"),
            (404, "Unknown session"),
            (404, "Unknown session"),
        ]

        with pytest.raises(HTTPException) as exc:
            await _authorized_v4_session(
                make_request("owner-principal"),
                dao,
                access_control,
                ended_session_id,
                level="WRITE",
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == "Session is not active"

        session = await _authorized_v4_session(
            make_request("owner-principal"),
            dao,
            access_control,
            active_session_id,
            level="WRITE",
        )
        assert session["session_id"] == active_session_id
        await access_control.close()
    finally:
        await _close_test_env(sql, vector)
