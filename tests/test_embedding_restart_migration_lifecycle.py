"""Durable restart, rebuild and cutover proof for embedding-space migration."""

from __future__ import annotations

import hashlib

import pytest

from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_memory.embedding.service import EmbeddingIdentity, EmbeddingService
from mesa_storage.dao import MemoryDAO
from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.kuzu_setup import initialize_schema_artifact
from mesa_storage.projection_generations import (
    ProjectionGenerationIdentityMismatchError,
    ProjectionGenerationRepository,
)
from mesa_storage.rebuild_cutover import (
    ParityGatedActivator,
    default_graph_verification_factory,
)
from mesa_storage.rebuild_preparation import OfflineRebuildPreparer
from mesa_storage.rebuild_replay import ProjectionReplayer
from mesa_storage.repositories.operations import OperationRepository
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine
from mesa_storage.writer_lock import StorageWriterLock
from mesa_workers.projection_worker import process_projection_outbox_once


def _provider(dimension: int):
    def embed(text: str) -> list[float]:
        seed = sum(text.encode("utf-8")) or 1
        return [float((seed + index * 17) % 101 + 1) for index in range(dimension)]

    return embed


def _manifest(identity: EmbeddingIdentity) -> dict[str, object]:
    return {
        **identity.as_dict(),
        "embedding_provider": identity.provider,
        "embedding_model": identity.model,
        "embedding_version": identity.version,
    }


def _candidate(raw_log_id: int, identity: EmbeddingIdentity) -> dict[str, object]:
    return MemoryCandidate.from_raw_log(
        raw_log_id=raw_log_id,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        dataset_id="dataset-a",
        document_id=f"document-{raw_log_id}",
        revision_id=f"revision-{raw_log_id}",
        chunk_id=f"chunk-{raw_log_id}",
        source_ref=f"source-{raw_log_id}",
        agent_id="agent-a",
        session_id="session-a",
        content_payload="MESA uses durable embedding generations.",
        embedding_provider=identity.provider,
        embedding_model=identity.model,
        embedding_version=identity.version,
        embedding_dimension=identity.dimension,
        embedding_space_id=identity.embedding_space_id,
        embedding_model_revision=identity.model_revision,
        embedding_normalized=identity.normalized,
        validation_mode=0,
    ).as_consolidation_record()


async def _admit(
    dao: MemoryDAO, *, raw_log_id: int, identity: EmbeddingIdentity
) -> dict[str, object]:
    candidate = _candidate(raw_log_id, identity)
    await dao.record_mutation(candidate, raw_log_id=raw_log_id)
    await dao.record_mutation_extraction(
        "agent-a",
        str(candidate["mutation_id"]),
        [
            {
                "head": "MESA",
                "relation": "USES",
                "tail": "durable embedding generations",
                "fact_text": "MESA uses durable embedding generations.",
                "source_span": "MESA uses durable embedding generations.",
            }
        ],
    )
    await dao.set_mutation_state("agent-a", str(candidate["mutation_id"]), "VALIDATED")
    return candidate


@pytest.mark.asyncio
async def test_restart_safe_admission_migration_rebuild_cutover_lifecycle(
    tmp_path,
) -> None:
    """A pending old-space admission cannot be embedded after a new-space restart."""
    trusted = tmp_path / "trusted"
    storage = trusted / "storage"
    work = trusted / "work"
    storage.mkdir(parents=True)
    work.mkdir()
    database = storage / "mesa.db"
    old = EmbeddingIdentity(provider="local-old", model="old-model", dimension=384)
    new = EmbeddingIdentity(
        provider="local-new", model="magibu-like-new", dimension=768
    )
    old_service = EmbeddingService(identity=old, provider_fn=_provider(old.dimension))

    sql = AsyncEngine(str(database))
    old_vector = VectorEngine(
        str(storage / "vector.lance"), embedding_service=old_service
    )
    old_graph_path = storage / "kuzu_db"
    await sql.initialize()
    await initialize_schema(sql)
    generations = ProjectionGenerationRepository(sql)
    await generations.assert_active_embedding_identity(old.as_dict())
    await old_vector.initialize()
    initialize_schema_artifact(str(old_graph_path))
    old_graph = KuzuGraphProvider(str(old_graph_path))
    await old_graph.initialize()
    old_dao = MemoryDAO(
        sqlite_engine=sql, vector_engine=old_vector, graph_provider=old_graph
    )
    try:
        await _admit(old_dao, raw_log_id=1, identity=old)
        for _ in range(3):
            assert (await process_projection_outbox_once(old_dao))["completed"] == 1

        pending = await _admit(old_dao, raw_log_id=2, identity=old)
        pending_mutation_id = str(pending["mutation_id"])
        persisted = await old_dao.get_projection_mutation(pending_mutation_id)
        assert persisted is not None
        assert persisted["embedding_identity_snapshot"] == old.as_dict()
    finally:
        await old_graph.close()
        await old_vector.close()
        await sql.close()

    # Fresh composition over the same durable storage simulates a process restart.
    restarted_sql = AsyncEngine(str(database))
    await restarted_sql.initialize()
    restarted_generations = ProjectionGenerationRepository(restarted_sql)
    with pytest.raises(ProjectionGenerationIdentityMismatchError, match="differs"):
        await restarted_generations.assert_active_embedding_identity(new.as_dict())
    assert (
        await restarted_generations.resolve_active(
            storage_root=storage, trusted_root=trusted
        )
    ).generation_id == "legacy"

    new_service = EmbeddingService(identity=new, provider_fn=_provider(new.dimension))
    restarted_vector = VectorEngine(
        str(storage / "vector.lance"), embedding_service=new_service
    )
    restarted_graph = KuzuGraphProvider(str(old_graph_path))
    await restarted_vector.initialize()
    await restarted_graph.initialize()
    restarted_dao = MemoryDAO(
        sqlite_engine=restarted_sql,
        vector_engine=restarted_vector,
        graph_provider=restarted_graph,
    )
    try:
        # SQL may finish; the vector lane must fail closed on the durable snapshot.
        assert (await process_projection_outbox_once(restarted_dao))["completed"] == 1
        protected = await process_projection_outbox_once(restarted_dao)
        assert protected["retry_pending"] == 1
        assertions = await restarted_dao.list_v4_assertions_for_mutation(
            pending_mutation_id
        )
        assert len(assertions) == 1
        assert str(
            assertions[0]["assertion_id"]
        ) not in await restarted_vector.get_active_node_ids("agent-a")
        assert (
            await restarted_generations.resolve_active(
                storage_root=storage, trusted_root=trusted
            )
        ).generation_id == "legacy"
    finally:
        await restarted_graph.close()
        await restarted_vector.close()
        await restarted_sql.close()

    # The migration operation is deliberately admitted only after the durable
    # old-space backlog drains under its original identity.  This is a second
    # real composition, not a mutation of the persisted admission snapshot.
    drain_sql = AsyncEngine(str(database))
    drain_vector = VectorEngine(
        str(storage / "vector.lance"), embedding_service=old_service
    )
    drain_graph = KuzuGraphProvider(str(old_graph_path))
    await drain_sql.initialize()
    await drain_vector.initialize()
    await drain_graph.initialize()
    drain_dao = MemoryDAO(
        sqlite_engine=drain_sql, vector_engine=drain_vector, graph_provider=drain_graph
    )
    try:
        assert (await process_projection_outbox_once(drain_dao))["completed"] == 1
        assert (await process_projection_outbox_once(drain_dao))["completed"] == 1
    finally:
        await drain_graph.close()
        await drain_vector.close()
        await drain_sql.close()

    # Rebuild targets the new space while the old ACTIVE generation remains online.
    rebuild_sql = AsyncEngine(str(database))
    await rebuild_sql.initialize()
    operations = OperationRepository(rebuild_sql)
    rebuild_generations = ProjectionGenerationRepository(rebuild_sql)
    submitted = await operations.submit(
        requested_by_principal_id="admin-a",
        idempotency_key="restart-migration-lifecycle",
        payload_hash=hashlib.sha256(b"restart-migration").hexdigest(),
    )
    claimed = await operations.claim(submitted["operation_id"], runner_id="runner-a")

    def verification_vector(path):
        service = old_service if path == storage / "vector.lance" else new_service
        return VectorEngine(
            str(path), embedding_service=service, allow_model_loading=False
        )

    try:
        with StorageWriterLock.acquire(storage, owner="rebuild-lifecycle") as lock:
            preparation = await OfflineRebuildPreparer(
                operations, rebuild_generations
            ).prepare(
                trusted_root=trusted,
                storage_root=storage,
                work_root=work,
                operation=claimed,
                runner_id="runner-a",
                writer_lock=lock,
                provider_manifest=_manifest(new),
            )
            assert preparation.generation["lifecycle_state"] == "STAGING"
            assert (
                await rebuild_generations.resolve_active(
                    storage_root=storage, trusted_root=trusted
                )
            ).generation_id == "legacy"
            replay = await ProjectionReplayer(operations).replay(
                preparation=preparation,
                trusted_root=trusted,
                storage_root=storage,
                runner_id="runner-a",
                provider_manifest=_manifest(new),
                embedding_service=new_service,
                allow_model_loading=False,
            )
            cutover = await ParityGatedActivator(
                operations, rebuild_generations
            ).activate(
                preparation=preparation,
                replay=replay,
                trusted_root=trusted,
                storage_root=storage,
                runner_id="runner-a",
                vector_factory=verification_vector,
                graph_factory=default_graph_verification_factory,
            )
        assert cutover.active_generation_id == preparation.target_generation_id
        async with rebuild_sql.connection() as connection:
            cursor = await connection.execute(
                "SELECT COUNT(*) FROM projection_generations WHERE lifecycle_state = 'ACTIVE'"
            )
            assert int((await cursor.fetchone())[0]) == 1
    finally:
        await rebuild_sql.close()

    # A second fresh composition restores the new generation and uses it for I/O.
    post_sql = AsyncEngine(str(database))
    await post_sql.initialize()
    post_generations = ProjectionGenerationRepository(post_sql)
    await post_generations.assert_active_embedding_identity(new.as_dict())
    active = await post_generations.resolve_active(
        storage_root=storage, trusted_root=trusted
    )
    assert active.generation_id == preparation.target_generation_id
    assert active.previous_generation_id == "legacy"
    post_vector = VectorEngine(str(active.vector_path), embedding_service=new_service)
    post_graph = KuzuGraphProvider(str(active.graph_path))
    await post_vector.initialize()
    await post_graph.initialize()
    post_dao = MemoryDAO(
        sqlite_engine=post_sql, vector_engine=post_vector, graph_provider=post_graph
    )
    try:
        current = await _admit(post_dao, raw_log_id=3, identity=new)
        for _ in range(3):
            assert (await process_projection_outbox_once(post_dao))["completed"] == 1
        results = await post_dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query="durable embedding generations",
            limit=5,
        )
        assert results
        assert str(current["mutation_id"]) != pending_mutation_id
    finally:
        await post_graph.close()
        await post_vector.close()
        await post_sql.close()


@pytest.mark.asyncio
async def test_restart_fences_same_dimension_different_embedding_spaces(
    tmp_path,
) -> None:
    database = tmp_path / "mesa.db"
    sql = AsyncEngine(str(database))
    await sql.initialize()
    await initialize_schema(sql)
    generations = ProjectionGenerationRepository(sql)
    first = EmbeddingIdentity(provider="local-a", model="model-a", dimension=768)
    second = EmbeddingIdentity(provider="local-b", model="model-b", dimension=768)
    try:
        await generations.assert_active_embedding_identity(first.as_dict())
        with pytest.raises(ProjectionGenerationIdentityMismatchError, match="differs"):
            await generations.assert_active_embedding_identity(second.as_dict())
    finally:
        await sql.close()
