"""Public contracts used by MESA E2E scope and graph certification."""

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from test_v4_retrieval_hardening_phase1 import _create_committed_mutation

from mesa_api.v4_router import create_v4_router
from mesa_storage.dao import MemoryDAO
from mesa_storage.retrieval_scope import stable_graph_path_id
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


def test_scope_audit_is_emitted_before_canonical_fusion():
    source = inspect.getsource(MemoryDAO.search_v4_memory)
    assert source.index("certification_metadata.update") < source.index(
        "rrf_fuse_lanes("
    )


async def _environment(tmp_path):
    sql = AsyncEngine(str(tmp_path / "certification.sqlite"))
    await sql.initialize()
    await initialize_schema(sql)
    vector = SimpleNamespace(
        compute_embedding=AsyncMock(return_value=[1.0, 0.0]),
        compute_query_embedding=AsyncMock(return_value=[1.0, 0.0]),
        upsert=AsyncMock(),
        search=AsyncMock(return_value=[]),
    )
    graph = SimpleNamespace(
        insert_node=AsyncMock(),
        insert_assertion=AsyncMock(),
        link_assertions=AsyncMock(),
        search_v4_graph=AsyncMock(return_value=[]),
        is_operational=True,
    )
    return sql, vector, graph, MemoryDAO(sql, vector, graph)


async def _add(dao, number, *, tenant, agent, dataset, subject="Shared policy"):
    return await asyncio.wait_for(
        _create_committed_mutation(
            dao,
            raw_log_id=number,
            tenant_id=tenant,
            agent_id=agent,
            dataset_id=dataset,
            chunk_id=f"chunk-{number}",
            document_id=f"document-{number}",
            content=f"{subject} applies",
            subject=subject,
            predicate="applies",
            object_value=f"Target {number}",
            evidence_span=f"{subject} applies",
        ),
        timeout=10,
    )


@pytest.mark.asyncio
async def test_scope_audit_is_pre_rank_deterministic_and_scope_sensitive(tmp_path):
    sql, _vector, _graph, dao = await _environment(tmp_path)
    try:
        allowed = await _add(
            dao, 1, tenant="tenant-a", agent="agent-a", dataset="dataset-a"
        )
        baseline = await dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="Shared policy",
        )
        await _add(dao, 2, tenant="tenant-a", agent="agent-b", dataset="dataset-a")
        before_cross_tenant = {}
        await dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="Shared policy",
            certification_metadata=before_cross_tenant,
        )
        await _add(dao, 3, tenant="tenant-b", agent="agent-b", dataset="dataset-b")

        first_metadata = {}
        first = await asyncio.wait_for(
            dao.search_v4_memory(
                tenant_id="tenant-a",
                agent_id="agent-a",
                dataset_ids=["dataset-a"],
                query="Shared policy",
                certification_metadata=first_metadata,
            ),
            timeout=10,
        )
        second_metadata = {}
        second = await asyncio.wait_for(
            dao.search_v4_memory(
                tenant_id="tenant-a",
                agent_id="agent-a",
                dataset_ids=["dataset-a"],
                query="Shared policy",
                certification_metadata=second_metadata,
            ),
            timeout=10,
        )

        assert [row["assertion_id"] for row in first] == [allowed["assertion_id"]]
        assert [
            (row["assertion_id"], row["rrf_score"], row["final_score"]) for row in first
        ] == [
            (row["assertion_id"], row["rrf_score"], row["final_score"])
            for row in baseline
        ]
        assert first == second
        assert first_metadata == second_metadata
        assert first_metadata == before_cross_tenant
        audit = first_metadata["scope_audit"]
        assert audit["contract_version"] == "mesa.scope-audit.v1"
        assert audit["enforcement_stage"] == "pre_rank"
        assert audit["evaluated_candidate_count"] == 2
        assert audit["eligible_candidate_count"] == 1
        assert audit["excluded_candidate_count"] == 1
        assert first[0]["scope_identity"] == {
            "tenant_id": "tenant-a",
            "dataset_id": "dataset-a",
            "agent_id": "agent-a",
            "jurisdiction": "",
            "status": "ACTIVE",
        }
        serialized = repr({"results": first, **first_metadata})
        assert "dataset_physical_id" not in serialized
        assert "registry_id" not in serialized
        assert "mutation_id" not in first[0]["scope_identity"]

        unaudited = await dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="Shared policy",
        )
        assert [
            (row["assertion_id"], row["rrf_score"], row["final_score"]) for row in first
        ] == [
            (row["assertion_id"], row["rrf_score"], row["final_score"])
            for row in unaudited
        ]

        changed_metadata = {}
        await asyncio.wait_for(
            dao.search_v4_memory(
                tenant_id="tenant-a",
                agent_id="agent-b",
                dataset_ids=["dataset-a"],
                query="Shared policy",
                certification_metadata=changed_metadata,
            ),
            timeout=10,
        )
        assert (
            changed_metadata["scope_audit"]["requested_scope_identity"]
            != audit["requested_scope_identity"]
        )
        assert (
            changed_metadata["scope_audit"]["exclusion_audit_hash"]
            != audit["exclusion_audit_hash"]
        )
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_graph_ablation_uses_same_fusion_path_and_preserves_pair_identity(
    tmp_path, monkeypatch
):
    sql, _vector, graph, dao = await _environment(tmp_path)
    try:
        fact = await _add(
            dao, 1, tenant="tenant-a", agent="agent-a", dataset="dataset-a"
        )
        graph.search_v4_graph.return_value = [
            {
                "entity_id": fact["object_entity_id"],
                "path_assertion_ids": [fact["assertion_id"]],
                "path_entity_ids": [fact["subject_id"], fact["object_entity_id"]],
                "score": 1.0,
            }
        ]

        import mesa_storage.dao as dao_module

        original_fuse = dao_module.rrf_fuse_lanes
        fused_lanes = []

        def recording_fuse(lanes, **kwargs):
            fused_lanes.append({name: list(values) for name, values in lanes.items()})
            return original_fuse(lanes, **kwargs)

        monkeypatch.setattr(dao_module, "rrf_fuse_lanes", recording_fuse)
        enabled_metadata = {}
        enabled = await dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="Shared policy",
            graph_enabled=True,
            certification_metadata=enabled_metadata,
        )
        disabled_metadata = {}
        disabled = await dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="Shared policy",
            graph_enabled=False,
            certification_metadata=disabled_metadata,
        )

        assert graph.search_v4_graph.await_count == 1
        assert len(fused_lanes) == 2
        assert fused_lanes[1]["graph"] == []
        assert {
            key: fused_lanes[0][key] for key in ("vector", "bm25", "assertion")
        } == {key: fused_lanes[1][key] for key in ("vector", "bm25", "assertion")}
        assert [row["assertion_id"] for row in enabled] == [
            row["assertion_id"] for row in disabled
        ]
        enabled_contract = enabled_metadata["graph_ablation"]
        disabled_contract = disabled_metadata["graph_ablation"]
        assert enabled_contract["mode"] == "enabled"
        assert disabled_contract["mode"] == "disabled"
        assert enabled_contract["pair_identity"] == disabled_contract["pair_identity"]
        changed_metadata = {}
        await dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="Different query",
            graph_enabled=False,
            certification_metadata=changed_metadata,
        )
        assert (
            changed_metadata["graph_ablation"]["pair_identity"]
            != disabled_contract["pair_identity"]
        )
        changed_config_metadata = {}
        await dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="Shared policy",
            limit=2,
            graph_enabled=False,
            certification_metadata=changed_config_metadata,
        )
        assert (
            changed_config_metadata["graph_ablation"]["pair_identity"]
            != disabled_contract["pair_identity"]
        )
        path = enabled[0]["retrieval_provenance"]["graph_paths"][0]
        assert (
            path["graph_path_id"] == enabled[0]["retrieval_provenance"]["graph_path_id"]
        )
    finally:
        await sql.close()


def test_graph_path_identity_is_stable_and_direction_sensitive():
    path = {
        "assertion_ids": ["assertion-a"],
        "entity_ids": ["entity-a", "entity-b"],
        "edge_directions": ["forward"],
        "predicates": ["applies"],
    }
    assert stable_graph_path_id(**path) == stable_graph_path_id(**path)
    assert stable_graph_path_id(**path) != stable_graph_path_id(
        **{**path, "edge_directions": ["reverse"]}
    )


@pytest.mark.asyncio
async def test_http_contract_validates_graph_mode_and_serializes_certification():
    dao = MagicMock()
    dao.rebuild_admission.is_pending = AsyncMock(return_value=False)
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

    async def search_v4_memory(**kwargs):
        metadata = kwargs["certification_metadata"]
        metadata.update(
            {
                "scope_audit": {
                    "contract_version": "mesa.scope-audit.v1",
                    "enforcement_stage": "pre_rank",
                    "requested_scope": {"tenant_id": "tenant-a"},
                    "requested_scope_identity": "sha256:scope",
                    "query_identity": "sha256:query",
                    "evaluated_candidate_count": 1,
                    "excluded_candidate_count": 0,
                    "eligible_candidate_count": 1,
                    "exclusion_audit_hash": "sha256:audit",
                },
                "graph_ablation": {
                    "contract_version": "mesa.graph-ablation.v1",
                    "mode": "disabled",
                    "pair_identity": "sha256:pair",
                    "query_identity": "sha256:query",
                    "retrieval_config_identity": "sha256:config",
                    "scope_identity": "sha256:scope",
                },
            }
        )
        return [{"retrieval_provenance": {"graph_path_id": "sha256:path"}}]

    dao.search_v4_memory = AsyncMock(side_effect=search_v4_memory)
    access = MagicMock()
    access.check_principal_session_access = AsyncMock(return_value=True)
    access.check_access = AsyncMock(return_value=True)
    access.check_scope_role = AsyncMock(return_value=True)
    access.check_dataset_permission = AsyncMock(return_value=True)

    async def principal(request: Request):
        request.state.principal = SimpleNamespace(
            principal_id="principal-a", status="active"
        )

    app = FastAPI(dependencies=[Depends(principal)])
    app.include_router(
        create_v4_router(get_dao=lambda: dao, get_access_control=lambda: access)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        invalid = await client.post(
            "/v4/memory/search",
            json={"session_id": "session-a", "query": "q", "graph_mode": "maybe"},
        )
        response = await client.post(
            "/v4/memory/search",
            json={
                "session_id": "session-a",
                "query": "q",
                "graph_mode": "disabled",
            },
        )

    assert invalid.status_code == 422
    assert response.status_code == 200
    body = response.json()
    assert body["scope_audit"]["contract_version"] == "mesa.scope-audit.v1"
    assert body["graph_ablation"]["mode"] == "disabled"
    kwargs = dao.search_v4_memory.await_args.kwargs
    assert kwargs["graph_enabled"] is False
    assert kwargs["request_principal_id"] == "principal-a"
