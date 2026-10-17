"""Full-flow MVP Core verification test for MESA Core (Task T08).

Validates:
1. End-to-end API admission (session -> catalog -> source-chunk -> insert).
2. Controlled model extraction (MOCK) -> durable outbox staging in SQLite.
3. Real worker processing (projection_worker) writing to real native storage:
   - SQLite (assertions, entities, mutations, catalog)
   - LanceDB native (vector engine on disk)
   - Kùzu native (graph provider on disk)
4. State transitions: PENDING -> CLAIMED -> COMMITTED / TERMINAL.
5. Scoped search and context extraction:
   - Two tenants (tenant-a, tenant-b), verifying strict data/provenance isolation.
6. Negative scenarios:
   - Unauthorized access / wrong owner (403 Forbidden).
   - Graph lane ablation (graph_mode="disabled").
   - Rollback of mutation.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import Depends, FastAPI, Request

from mesa_api.v4_router import create_v4_router
from mesa_memory.config import configured_embedding_identity
from mesa_memory.embedding.service import EmbeddingService
from mesa_memory.security.rbac import AccessControl
from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.kuzu_setup import initialize_schema_artifact
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine
from mesa_workers.projection_worker import (
    process_artifact_cleanup_once,
    process_projection_outbox_once,
)


@pytest.mark.asyncio
async def test_full_mvp_core_lifecycle_smoke(tmp_path):
    # -----------------------------------------------------------------------
    # Step 1: Storage & Environment Setup (Real SQLite, LanceDB, Kùzu)
    # -----------------------------------------------------------------------
    sql_path = tmp_path / "mesa_core.db"
    lance_path = tmp_path / "vectors.lance"
    kuzu_path = tmp_path / "graph"
    rbac_path = tmp_path / "rbac.db"

    sql = AsyncEngine(str(sql_path), max_connections=4)
    await sql.initialize()
    await initialize_schema(sql)

    identity = configured_embedding_identity()
    vector = VectorEngine(
        str(lance_path),
        max_workers=1,
        embedding_service=EmbeddingService(
            identity=identity,
            provider_fn=lambda t: [0.125] * identity.dimension,
            query_provider_fn=lambda t: [0.125] * identity.dimension,
        ),
    )
    await vector.initialize()

    initialize_schema_artifact(str(kuzu_path))
    graph = KuzuGraphProvider(str(kuzu_path), max_workers=1)
    await graph.initialize()

    dao = MemoryDAO(sqlite_engine=sql, vector_engine=vector, graph_provider=graph)

    # Setup AccessControl with active principals
    access_control = AccessControl(policy_path=str(rbac_path))
    await access_control.initialize()

    current_principal_id = "principal-a"

    async def get_active_principal(request: Request):
        request.state.principal = SimpleNamespace(
            principal_id=current_principal_id,
            principal_type="USER",
            status="active",
        )

    app = FastAPI(dependencies=[Depends(get_active_principal)])
    app.include_router(
        create_v4_router(
            get_dao=lambda: dao,
            get_access_control=lambda: access_control,
        )
    )

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    client = httpx.AsyncClient(transport=transport, base_url="http://mesa-test")

    try:
        # Seed RBAC: principal-a has full access to tenant-a, principal-b to tenant-b
        await access_control.grant_principal_permission(
            "principal-a", "agent-a", "SESSION_CREATE"
        )
        await access_control.grant_principal_permission("principal-a", "agent-a", "READ")
        await access_control.grant_principal_permission("principal-a", "agent-a", "WRITE")
        await access_control.grant_scope_role(
            "principal-a", tenant_id="tenant-a", role="OWNER"
        )
        await access_control.grant_scope_role(
            "principal-a",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            dataset_id="dataset-a",
            role="OWNER",
        )
        await access_control.grant_dataset_permission(
            "principal-a",
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            permission="ROLLBACK",
        )

        await access_control.grant_principal_permission(
            "principal-b", "agent-b", "SESSION_CREATE"
        )
        await access_control.grant_principal_permission("principal-b", "agent-b", "READ")
        await access_control.grant_principal_permission("principal-b", "agent-b", "WRITE")
        await access_control.grant_scope_role(
            "principal-b", tenant_id="tenant-b", role="OWNER"
        )
        await access_control.grant_scope_role(
            "principal-b",
            tenant_id="tenant-b",
            workspace_id="workspace-b",
            dataset_id="dataset-b",
            role="OWNER",
        )

        # Seed catalog scopes
        await dao.ensure_v4_catalog_scope(
            tenant_id="tenant-a", workspace_id="workspace-a", dataset_id="dataset-a"
        )
        await dao.ensure_v4_catalog_scope(
            tenant_id="tenant-b", workspace_id="workspace-b", dataset_id="dataset-b"
        )

        # -------------------------------------------------------------------
        # Step 2: Session & Catalog Admission for Tenant A
        # -------------------------------------------------------------------
        current_principal_id = "principal-a"

        # 2a: Start Session
        sess_resp = await client.post(
            "/v4/sessions/start",
            json={
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "dataset_ids": ["dataset-a"],
                "agent_id": "agent-a",
            },
        )
        assert sess_resp.status_code == 201, sess_resp.text
        session_a_id = sess_resp.json()["session_id"]

        # 2b: Create Document, Revision and Source Chunk
        doc_resp = await client.post(
            "/v4/catalog/documents",
            json={
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "dataset_id": "dataset-a",
                "document_id": "doc-kvkk",
                "title": "KVKK Kanunu",
            },
        )
        assert doc_resp.status_code == 201, doc_resp.text

        rev_resp = await client.post(
            "/v4/catalog/revisions",
            json={
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "dataset_id": "dataset-a",
                "document_id": "doc-kvkk",
                "revision_id": "rev-1",
                "revision_number": 1,
                "content_sha256": "a" * 64,
            },
        )
        assert rev_resp.status_code == 201, rev_resp.text

        chunk_resp = await client.post(
            "/v4/catalog/source-chunks",
            json={
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "dataset_id": "dataset-a",
                "document_id": "doc-kvkk",
                "revision_id": "rev-1",
                "chunk_id": "chunk-10",
                "title": "KVKK Madde 10",
                "content": "Veri sorumlusu aydinlatma yukumlulugunu yerine getirmekle yukumludur.",
                "source_ref": "KVKK/10",
            },
        )
        assert chunk_resp.status_code == 201, chunk_resp.text

        # 2c: Ingest Memory with Legal Identity
        insert_resp = await client.post(
            "/v4/memory/insert",
            json={
                "session_id": session_a_id,
                "dataset_id": "dataset-a",
                "document_id": "doc-kvkk",
                "revision_id": "rev-1",
                "chunk_id": "chunk-10",
                "title": "KVKK Madde 10",
                "source_ref": "KVKK/10",
                "content": "Veri sorumlusu aydinlatma yukumlulugunu yerine getirmekle yukumludur.",
                "evidence_span": "aydinlatma yukumlulugunu yerine getirmekle yukumludur",
                "legal_identity": {
                    "law_id": "6098",
                    "statute_code": "TBK",
                    "article_id": "1",
                },
            },
        )
        assert insert_resp.status_code == 202, insert_resp.text
        insert_data = insert_resp.json()
        mutation_a_id = insert_data["mutation_id"]
        assert insert_data["status"] == "accepted"

        # -------------------------------------------------------------------
        # Step 3: Outbox Processing by Real Worker -> Native Storage
        # -------------------------------------------------------------------
        # Extraction staging: record triplet and validate mutation
        await dao.record_mutation_extraction(
            "agent-a",
            mutation_a_id,
            [
                {
                    "head": "Veri Sorumlusu",
                    "relation": "YUKUMLUDUR",
                    "tail": "Aydinlatma",
                    "confidence": 0.95,
                }
            ],
        )
        await dao.set_mutation_state("agent-a", mutation_a_id, "VALIDATED")

        # Process outbox items until drained
        drained = False
        for _ in range(5):
            batch_res = await process_projection_outbox_once(
                dao, worker_id="mvp-worker-1", limit=10
            )
            if batch_res["claimed"] == 0:
                drained = True
                break

        assert drained is True

        # Verify mutation is COMMITTED in SQLite
        mut_summary = await dao.get_mutation_summary(mutation_a_id)
        assert mut_summary is not None
        assert mut_summary["state"] == "COMMITTED"

        # Verify physical LanceDB vector presence
        lance_vectors = await vector.get_active_node_ids()
        assert len(lance_vectors) >= 1

        # Verify physical Kùzu graph presence
        kuzu_assertions = await graph.execute_query(
            "MATCH (a:Assertion) RETURN count(a) AS cnt"
        )
        assert kuzu_assertions[0][0] >= 1

        # -------------------------------------------------------------------
        # Step 4: Scoped Search & Context for Tenant A
        # -------------------------------------------------------------------
        search_resp = await client.post(
            "/v4/memory/search",
            json={
                "session_id": session_a_id,
                "query": "Veri sorumlusu aydinlatma",
                "limit": 5,
            },
        )
        assert search_resp.status_code == 200, search_resp.text
        search_body = search_resp.json()
        assert len(search_body["results"]) >= 1
        top_res = search_body["results"][0]
        assert "Aydinlatma" in top_res["evidence_span"] or "aydinlatma" in top_res["evidence_span"]
        assert top_res["scope_identity"]["tenant_id"] == "tenant-a"
        assert top_res["scope_identity"]["dataset_id"] == "dataset-a"
        assert top_res["source_chunk_id"] == "chunk-10"
        assert top_res["document_id"] == "doc-kvkk"
        assert top_res["retrieval_provenance"]["assertion_id"] == top_res["assertion_id"]
        assert top_res["retrieval_provenance"]["source_chunk_id"] == "chunk-10"

        # Check /context endpoint
        ctx_resp = await client.get(
            f"/v4/sessions/{session_a_id}/context",
            params={"query": "aydinlatma yukumlulugu", "token_budget": 500},
        )
        assert ctx_resp.status_code == 200, ctx_resp.text
        ctx_body = ctx_resp.json()
        assert "aydinlatma" in ctx_body["context"].lower()
        assert ctx_body["estimated_token_count"] > 0

        # -------------------------------------------------------------------
        # Step 5: Negative Checks: Tenant B & Cross-Tenant Isolation
        # -------------------------------------------------------------------
        # Switch to principal-b (Tenant B)
        current_principal_id = "principal-b"

        sess_b_resp = await client.post(
            "/v4/sessions/start",
            json={
                "tenant_id": "tenant-b",
                "workspace_id": "workspace-b",
                "dataset_ids": ["dataset-b"],
                "agent_id": "agent-b",
            },
        )
        assert sess_b_resp.status_code == 201, sess_b_resp.text
        session_b_id = sess_b_resp.json()["session_id"]

        # 5a: Cross-tenant unauthorized access attempt
        # Tenant B attempts to search using Tenant A's session -> 403 Forbidden
        cross_search = await client.post(
            "/v4/memory/search",
            json={
                "session_id": session_a_id,
                "query": "aydinlatma",
            },
        )
        assert cross_search.status_code in (403, 404)

        # 5b: Tenant B searching its own empty dataset gets ZERO results from Tenant A
        b_search = await client.post(
            "/v4/memory/search",
            json={
                "session_id": session_b_id,
                "query": "aydinlatma",
            },
        )
        assert b_search.status_code == 200, b_search.text
        assert len(b_search.json()["results"]) == 0

        # Populate B as well, reusing A's public catalog IDs. Its physical
        # artifacts and evidence must survive A's restart and rollback.
        b_scope = {"tenant_id": "tenant-b", "workspace_id": "workspace-b", "dataset_id": "dataset-b"}
        for endpoint, payload in (
            ("documents", {"document_id": "doc-kvkk", "title": "B policy"}),
            ("revisions", {"document_id": "doc-kvkk", "revision_id": "rev-1", "revision_number": 1, "content_sha256": "b" * 64}),
            ("source-chunks", {"document_id": "doc-kvkk", "revision_id": "rev-1", "chunk_id": "chunk-10", "title": "B policy", "content": "Tenant B aydinlatma policy.", "source_ref": "B/policy"}),
        ):
            response = await client.post(f"/v4/catalog/{endpoint}", json={**b_scope, **payload})
            assert response.status_code == 201, response.text
        b_insert = await client.post("/v4/memory/insert", json={
            "session_id": session_b_id, "dataset_id": "dataset-b",
            "document_id": "doc-kvkk", "revision_id": "rev-1", "chunk_id": "chunk-10",
            "title": "B policy", "content": "Tenant B aydinlatma policy.",
            "source_ref": "B/policy", "evidence_span": "Tenant B aydinlatma policy.",
        })
        assert b_insert.status_code == 202, b_insert.text
        mutation_b_id = b_insert.json()["mutation_id"]
        await dao.record_mutation_extraction("agent-b", mutation_b_id, [
            {"head": "Tenant B", "relation": "HAS", "tail": "aydinlatma policy"},
        ])
        await dao.set_mutation_state("agent-b", mutation_b_id, "VALIDATED")
        for _ in range(3):
            result = await process_projection_outbox_once(dao, worker_id="mvp-worker-b", limit=10)
            assert result["retry_pending"] == result["dead_letter"] == 0
        b_vector_ids = await vector.get_active_node_ids("agent-b")
        assert len(b_vector_ids) == 1
        b_search = await client.post("/v4/memory/search", json={"session_id": session_b_id, "query": "aydinlatma"})
        assert b_search.status_code == 200, b_search.text
        assert len(b_search.json()["results"]) == 1
        b_evidence = b_search.json()["results"][0]
        assert b_evidence["scope_identity"]["tenant_id"] == "tenant-b"
        assert b_evidence["assertion_id"] != top_res["assertion_id"]

        # 5c: Cross-tenant rollback rejection
        cross_rollback = await client.post(
            f"/v4/mutations/{mutation_a_id}/rollback"
        )
        assert cross_rollback.status_code in (403, 404)

        # -------------------------------------------------------------------
        # Step 6: Negative Checks: Graph Mode Ablation & Rollback
        # -------------------------------------------------------------------
        current_principal_id = "principal-a"

        # 6a: Graph ablation (graph_mode="disabled")
        ablation_search = await client.post(
            "/v4/memory/search",
            json={
                "session_id": session_a_id,
                "query": "Veri sorumlusu aydinlatma",
                "graph_mode": "disabled",
            },
        )
        assert ablation_search.status_code == 200, ablation_search.text
        assert ablation_search.json()["graph_ablation"]["mode"] == "disabled"

        # Reopen all physical stores and retrieve through the same API session.
        await graph.close()
        await vector.close()
        await sql.close()
        sql = AsyncEngine(str(sql_path), max_connections=4)
        await sql.initialize()
        vector = VectorEngine(
            str(lance_path), max_workers=1,
            embedding_service=EmbeddingService(
                identity=identity,
                provider_fn=lambda t: [0.125] * identity.dimension,
                query_provider_fn=lambda t: [0.125] * identity.dimension,
            ),
        )
        await vector.initialize()
        graph = KuzuGraphProvider(str(kuzu_path), max_workers=1)
        await graph.initialize()
        dao = MemoryDAO(sqlite_engine=sql, vector_engine=vector, graph_provider=graph)
        restarted = await client.post(
            "/v4/memory/search",
            json={"session_id": session_a_id, "query": "aydinlatma"},
        )
        assert restarted.status_code == 200, restarted.text
        assert restarted.json()["results"][0]["assertion_id"] == top_res["assertion_id"]
        assert (await dao.reconcile_v4_projection_parity())["missing_artifacts"] == 0

        # 6b: Rollback of Tenant A's mutation
        rollback_resp = await client.post(
            f"/v4/mutations/{mutation_a_id}/rollback"
        )
        assert rollback_resp.status_code == 202, rollback_resp.text
        rollback_data = rollback_resp.json()
        assert rollback_data["state"] == "ROLLING_BACK"
        for _ in range(10):
            cleanup = await process_artifact_cleanup_once(dao, worker_id="mvp-cleanup", limit=10)
            assert cleanup["blocked"] == cleanup["retry_pending"] == 0
            if cleanup["claimed"] == 0:
                break
        assert (await dao.get_mutation_summary(mutation_a_id))["state"] == "ROLLED_BACK"
        assert await vector.get_active_node_ids() == b_vector_ids
        assert await graph.execute_query("MATCH (a:Assertion {agent_id: 'agent-a'}) RETURN count(a)") == [[0]]
        assert await graph.execute_query("MATCH (e:Entity {agent_id: 'agent-a'}) RETURN count(e)") == [[0]]
        assert await graph.execute_query("MATCH (a:Assertion {agent_id: 'agent-b'}) RETURN count(a)") == [[1]]
        after_rollback = await client.post(
            "/v4/memory/search",
            json={"session_id": session_a_id, "query": "aydinlatma"},
        )
        assert after_rollback.status_code == 200, after_rollback.text
        assert after_rollback.json()["results"] == []
        current_principal_id = "principal-b"
        preserved_b = await client.post("/v4/memory/search", json={"session_id": session_b_id, "query": "aydinlatma"})
        assert preserved_b.status_code == 200, preserved_b.text
        assert preserved_b.json()["results"][0]["assertion_id"] == b_evidence["assertion_id"]
        assert (await dao.get_mutation_summary(mutation_b_id))["state"] == "COMMITTED"

    finally:
        await client.aclose()
        await graph.close()
        await vector.close()
        await sql.close()
        await access_control.close()
