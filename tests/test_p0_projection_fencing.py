import uuid
from types import SimpleNamespace

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.mark.asyncio
async def test_projection_fencing_against_rollback(tmp_path):
    """Verify that completing a projection after rollback fails and cannot advance state."""
    db_path = tmp_path / "mesa_test.db"
    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())

    tenant_id = "tenant_test"
    agent_id = "agent_test"
    dataset_id = "dataset_test"
    document_id = "doc_test"
    session_id = "sess_1"
    pipeline_run_id = f"run_{uuid.uuid4().hex[:8]}"
    mutation_id = f"mut_{uuid.uuid4().hex[:8]}"
    candidate_id = f"cand_{uuid.uuid4().hex[:8]}"

    # 1. Create pipeline run in PROJECTING state and memory mutation
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, agent_id, workspace_id, dataset_id, session_id, state) "
            "VALUES (?, ?, ?, 'ws_default', ?, ?, 'PROJECTING')",
            (pipeline_run_id, tenant_id, agent_id, dataset_id, session_id),
        )
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, agent_id, dataset_id, document_id, session_id, pipeline_run_id, content_payload, state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', 'PENDING')",
            (mutation_id, candidate_id, tenant_id, agent_id, dataset_id, document_id, session_id, pipeline_run_id),
        )
        await db.execute(
            "INSERT INTO projection_outbox (projection_id, mutation_id, projection_name, state) "
            "VALUES (?, ?, 'SQL', 'PENDING')",
            (f"proj_{mutation_id}_SQL", mutation_id),
        )
        await db.commit()

    # 2. Claim projection
    claims = await dao.claim_projection_outbox(worker_id="worker_1", limit=10)
    assert len(claims) == 1
    claim = claims[0]
    assert claim["projection_id"] == f"proj_{mutation_id}_SQL"

    # 3. Request rollback of pipeline run while worker holds claim
    rollback_res = await dao.request_pipeline_rollback(pipeline_run_id)
    assert rollback_res["state"] == "ROLLED_BACK"

    # 4. Attempt to complete projection with stale claim_token
    success = await dao.complete_projection_outbox(
        claim["projection_id"],
        worker_id="worker_1",
        claim_token=claim["claim_token"],
        outcome="DONE",
    )
    assert success is False, "Stale projection completion must fail after rollback"

    # 5. Attempt to register artifact under rolled-back mutation
    with pytest.raises(ValueError, match="cannot register artifact"):
        await dao.record_mutation_artifact(
            mutation_id,
            store_name="SQL",
            artifact_kind="ENTITY",
            artifact_id="ent_1",
        )

    # 6. Verify mutation and pipeline run remain ROLLED_BACK
    async with engine.transaction() as db:
        async with db.execute("SELECT state FROM memory_mutations WHERE mutation_id = ?", (mutation_id,)) as cursor:
            row = await cursor.fetchone()
            assert row["state"] == "ROLLED_BACK"

        async with db.execute("SELECT state FROM pipeline_runs WHERE pipeline_run_id = ?", (pipeline_run_id,)) as cursor:
            row = await cursor.fetchone()
            assert row["state"] == "ROLLED_BACK"

    await engine.close()


@pytest.mark.asyncio
async def test_projection_parity_repair_cannot_race_rollback(tmp_path):
    """A maintenance repair discovered before rollback cannot resurrect it."""
    engine = AsyncEngine(str(tmp_path / "mesa_parity_race.db"))
    await engine.initialize()
    await initialize_schema(engine)
    pipeline_run_id = "run_parity_race"
    mutation_id = "mut_parity_race"
    projection_id = "proj_parity_race"

    class RollbackDuringVectorCheck:
        async def get_existing_node_ids(self, _agent_id, _node_ids):
            async with engine.transaction() as db:
                await db.execute(
                    "UPDATE pipeline_runs SET state = 'ROLLED_BACK' WHERE pipeline_run_id = ?",
                    (pipeline_run_id,),
                )
                await db.execute(
                    "UPDATE memory_mutations SET state = 'ROLLED_BACK' WHERE mutation_id = ?",
                    (mutation_id,),
                )
                await db.execute(
                    "UPDATE projection_outbox SET state = 'CANCELLED' WHERE projection_id = ?",
                    (projection_id,),
                )
                await db.commit()
            return set()

    dao = MemoryDAO(engine, RollbackDuringVectorCheck())
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, agent_id, workspace_id, dataset_id, session_id, state) "
            "VALUES (?, 'tenant', 'agent', 'workspace', 'dataset', 'session', 'COMMITTED')",
            (pipeline_run_id,),
        )
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, agent_id, dataset_id, session_id, pipeline_run_id, content_payload, state) "
            "VALUES (?, 'candidate', 'tenant', 'agent', 'dataset', 'session', ?, '{}', 'COMMITTED')",
            (mutation_id, pipeline_run_id),
        )
        await db.execute(
            "INSERT INTO projection_outbox (projection_id, mutation_id, projection_name, state) "
            "VALUES (?, ?, 'VECTOR', 'COMPLETED')",
            (projection_id, mutation_id),
        )
        await db.execute(
            "INSERT INTO memory_artifacts (artifact_row_id, mutation_id, store_name, artifact_kind, artifact_id, state) "
            "VALUES ('artifact-row', ?, 'VECTOR', 'ENTITY_VECTOR', 'entity', 'ACTIVE')",
            (mutation_id,),
        )
        await db.commit()

    result = await dao.reconcile_v4_projection_parity(repair=True)
    assert result["missing_artifacts"] == 1
    assert result["requeued_lanes"] == 0
    async with engine.connection() as db:
        mutation = await (
            await db.execute(
                "SELECT state FROM memory_mutations WHERE mutation_id = ?", (mutation_id,)
            )
        ).fetchone()
        projection = await (
            await db.execute(
                "SELECT state FROM projection_outbox WHERE projection_id = ?", (projection_id,)
            )
        ).fetchone()
    assert mutation[0] == "ROLLED_BACK"
    assert projection[0] == "CANCELLED"
    await engine.close()


@pytest.mark.asyncio
async def test_projection_parity_requeues_every_missing_lane_for_one_mutation(tmp_path):
    """One CAS transition must not prevent repair of a second missing lane."""
    engine = AsyncEngine(str(tmp_path / "mesa_parity_multi_lane.db"))
    await engine.initialize()
    await initialize_schema(engine)
    vector = SimpleNamespace(get_existing_node_ids=lambda *_args: _empty_ids())
    dao = MemoryDAO(engine, vector)
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, agent_id, workspace_id, dataset_id, session_id, state) "
            "VALUES ('run', 'tenant', 'agent', 'workspace', 'dataset', 'session', 'COMMITTED')"
        )
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, agent_id, dataset_id, session_id, pipeline_run_id, content_payload, state) "
            "VALUES ('mutation', 'candidate', 'tenant', 'agent', 'dataset', 'session', 'run', '{}', 'COMMITTED')"
        )
        for lane in ("SQL", "VECTOR"):
            await db.execute(
                "INSERT INTO projection_outbox (projection_id, mutation_id, projection_name, state) "
                "VALUES (?, 'mutation', ?, 'COMPLETED')",
                (f"projection-{lane}", lane),
            )
            await db.execute(
                "INSERT INTO memory_artifacts (artifact_row_id, mutation_id, store_name, artifact_kind, artifact_id, state) "
                "VALUES (?, 'mutation', ?, ?, ?, 'ACTIVE')",
                (
                    f"artifact-{lane}",
                    lane,
                    "ENTITY" if lane == "SQL" else "ENTITY_VECTOR",
                    f"missing-{lane}",
                ),
            )
        await db.commit()

    result = await dao.reconcile_v4_projection_parity(repair=True)
    assert result["missing_artifacts"] == 2
    assert result["requeued_lanes"] == 2
    async with engine.connection() as db:
        states = await (
            await db.execute(
                "SELECT projection_name, state FROM projection_outbox ORDER BY projection_name"
            )
        ).fetchall()
        mutation = await (
            await db.execute(
                "SELECT state FROM memory_mutations WHERE mutation_id = 'mutation'"
            )
        ).fetchone()
    assert [(row[0], row[1]) for row in states] == [
        ("SQL", "RETRY_PENDING"),
        ("VECTOR", "RETRY_PENDING"),
    ]
    assert mutation[0] == "RETRY_PENDING"
    await engine.close()


@pytest.mark.asyncio
async def test_physical_vector_and_graph_side_effect_compensated_on_fencing_loss(tmp_path):
    """Orchestrate physical write -> rollback -> complete_projection_outbox -> compensating deletion -> physical state absent."""
    db_path = tmp_path / "mesa_test_physical_comp.db"
    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    await initialize_schema(engine)

    stored_vectors: dict[str, list[float]] = {}
    stored_assertions: set[str] = set()

    class FakeVectorEngine:
        is_initialized = True

        async def compute_embedding(self, _text: str) -> list[float]:
            return [0.1] * 384

        async def upsert(self, node_id: str, agent_id: str, embedding: list[float], content_hash: str | None = None) -> None:
            stored_vectors[node_id] = embedding

        async def hard_delete(self, node_id: str, agent_id: str) -> None:
            stored_vectors.pop(node_id, None)

        async def get_active_node_ids(self, agent_id: str) -> list[str]:
            return list(stored_vectors.keys())

    class FakeGraphProvider:
        async def insert_node(self, subject_id: str, head: str, agent_id: str) -> None:
            pass

        async def insert_assertion(self, assertion_id: str, **_kwargs) -> None:
            stored_assertions.add(assertion_id)

        async def delete_assertions(self, agent_id: str, assertion_ids: list[str]) -> None:
            for aid in assertion_ids:
                stored_assertions.discard(aid)

        async def delete_nodes(self, **_kwargs) -> None:
            pass

        async def link_assertions(self, **_kwargs) -> None:
            pass

    vec_engine = FakeVectorEngine()
    graph_provider = FakeGraphProvider()
    dao = MemoryDAO(engine, vec_engine, graph_provider=graph_provider)

    tenant_id = "tenant_phys"
    agent_id = "agent_phys"
    workspace_id = "ws_phys"
    dataset_id = "dataset_phys"
    document_id = "doc_phys"
    pipeline_run_id = "run_phys_race"
    mutation_id = "mut_phys_race"

    await dao.create_v4_workspace(tenant_id=tenant_id, workspace_id=workspace_id, workspace_name="WS Phys")
    await dao.ensure_v4_catalog_scope(tenant_id=tenant_id, workspace_id=workspace_id, dataset_id=dataset_id)
    await dao.create_v4_document(tenant_id=tenant_id, dataset_id=dataset_id, title="Doc Phys", document_id=document_id)

    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, agent_id, workspace_id, dataset_id, session_id, state) "
            "VALUES (?, ?, ?, ?, ?, 'sess', 'PROJECTING')",
            (pipeline_run_id, tenant_id, agent_id, workspace_id, dataset_id),
        )
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, agent_id, dataset_id, document_id, revision_id, chunk_id, session_id, pipeline_run_id, source_ref, content_payload, state) "
            "VALUES (?, 'cand_p', ?, ?, ?, ?, 'rev_p', 'chk_p', 'sess', ?, 'ref_p', '{\"projection_triplets\": [{\"head\": \"A\", \"relation\": \"R\", \"tail\": \"B\"}]}', 'VALIDATED')",
            (mutation_id, tenant_id, agent_id, dataset_id, document_id, pipeline_run_id),
        )
        await db.execute(
            "INSERT INTO projection_outbox (projection_id, mutation_id, projection_name, state) "
            "VALUES (?, ?, 'SQL', 'PENDING')",
            (f"proj_sql_{mutation_id}", mutation_id),
        )
        await db.execute(
            "INSERT INTO projection_outbox (projection_id, mutation_id, projection_name, state) "
            "VALUES (?, ?, 'VECTOR', 'PENDING')",
            (f"proj_vec_{mutation_id}", mutation_id),
        )
        await db.execute(
            "INSERT INTO projection_outbox (projection_id, mutation_id, projection_name, state) "
            "VALUES (?, ?, 'GRAPH', 'PENDING')",
            (f"proj_graph_{mutation_id}", mutation_id),
        )
        await db.commit()

    # 1. Claim and complete SQL projection first (lane ordering requirement)
    sql_claims = await dao.claim_projection_outbox(worker_id="worker_phys", limit=1)
    assert len(sql_claims) == 1
    assert sql_claims[0]["projection_name"] == "SQL"
    await dao.complete_projection_outbox(sql_claims[0]["projection_id"], worker_id="worker_phys", claim_token=sql_claims[0]["claim_token"], outcome="APPLIED")

    # 2. Claim VECTOR projection outbox
    claims = await dao.claim_projection_outbox(worker_id="worker_phys", limit=1)
    assert len(claims) == 1
    assert claims[0]["projection_name"] == "VECTOR"

    # 2. Worker performs physical writes
    mut = await dao.get_projection_mutation(mutation_id)
    node_id = await dao.project_v4_vector_entity(mutation=mut, entity_name="A")
    assert node_id in stored_vectors

    triplet = {"head": "A", "relation": "R", "tail": "B"}
    assertion_id = await dao.project_v4_graph_triplet(mutation=mut, triplet=triplet)
    assert assertion_id in stored_assertions

    # 3. Rollback pipeline run in parallel (simulating race)
    await dao.request_pipeline_rollback(pipeline_run_id)

    # 4. Worker attempts complete_projection_outbox
    for claim in claims:
        success = await dao.complete_projection_outbox(
            str(claim["projection_id"]),
            worker_id="worker_phys",
            claim_token=str(claim["claim_token"]),
            outcome="APPLIED",
        )
        assert success is False

    # 5. VERIFY PHYSICAL STATE ABSENCE: Compensating physical deletion executed!
    assert node_id not in stored_vectors, "Physical vector must be deleted after rollback fencing loss"
    assert assertion_id not in stored_assertions, "Physical graph assertion must be deleted after rollback fencing loss"

    await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["VECTOR", "GRAPH"])
async def test_post_write_fence_loss_compensates_unowned_physical_state(tmp_path, lane):
    """Pause at the real secondary write, terminalize, then inspect every store."""
    engine = AsyncEngine(str(tmp_path / f"post_write_{lane}.db"))
    await engine.initialize()
    await initialize_schema(engine)
    vectors: set[str] = set()
    graph_nodes: set[str] = set()
    graph_assertions: set[str] = set()
    dao: MemoryDAO

    class Vector:
        is_initialized = True

        async def compute_embedding(self, _text: str) -> list[float]:
            return [0.1] * 384

        async def upsert(self, node_id: str, **_kwargs) -> None:
            vectors.add(node_id)
            if lane == "VECTOR":
                await dao.request_pipeline_rollback("run")

        async def hard_delete(self, node_id: str, _agent_id: str) -> None:
            vectors.discard(node_id)

    class Graph:
        async def insert_node(self, node_id: str, *_args) -> None:
            graph_nodes.add(node_id)

        async def insert_assertion(self, assertion_id: str, **_kwargs) -> None:
            graph_assertions.add(assertion_id)
            if lane == "GRAPH":
                await dao.request_pipeline_rollback("run")

        async def delete_assertions(self, *, assertion_ids: list[str], **_kwargs) -> None:
            graph_assertions.difference_update(assertion_ids)

        async def delete_nodes(self, *, node_ids: list[str], **_kwargs) -> None:
            graph_nodes.difference_update(node_ids)

        async def link_assertions(self, **_kwargs) -> None:
            return None

    dao = MemoryDAO(engine, Vector(), graph_provider=Graph())
    await dao.create_v4_workspace(tenant_id="tenant", workspace_id="ws", workspace_name="WS")
    await dao.ensure_v4_catalog_scope(tenant_id="tenant", workspace_id="ws", dataset_id="data")
    await dao.create_v4_document(tenant_id="tenant", dataset_id="data", document_id="doc", title="Doc")
    async with engine.transaction() as db:
        await db.execute(
            "INSERT INTO pipeline_runs (pipeline_run_id, tenant_id, agent_id, workspace_id, dataset_id, session_id, state) "
            "VALUES ('run', 'tenant', 'agent', 'ws', 'data', 'session', 'PROJECTING')"
        )
        await db.execute(
            "INSERT INTO memory_mutations (mutation_id, candidate_id, tenant_id, agent_id, dataset_id, document_id, revision_id, chunk_id, session_id, pipeline_run_id, source_ref, content_payload, state) "
            "VALUES ('mutation', 'candidate', 'tenant', 'agent', 'data', 'doc', 'revision', 'chunk', 'session', 'run', 'source', '{}', 'VALIDATED')"
        )
        await db.commit()
    mutation = await dao.get_projection_mutation("mutation")
    assert mutation is not None

    with pytest.raises(ValueError, match="cannot register artifact"):
        if lane == "VECTOR":
            await dao.project_v4_vector_entity(mutation=mutation, entity_name="Alpha")
        else:
            await dao.project_v4_graph_triplet(
                mutation=mutation,
                triplet={"head": "Alpha", "relation": "uses", "tail": "Beta"},
            )

    assert not vectors
    assert not graph_nodes
    assert not graph_assertions
    async with engine.connection() as db:
        assertion_count = await (
            await db.execute("SELECT COUNT(*) FROM v4_assertions WHERE mutation_id = 'mutation'")
        ).fetchone()
        artifacts = await (
            await db.execute("SELECT COUNT(*) FROM memory_artifacts WHERE mutation_id = 'mutation'")
        ).fetchone()
    assert assertion_count[0] == 0
    assert artifacts[0] == 0
    await engine.close()


async def _empty_ids() -> set[str]:
    return set()
