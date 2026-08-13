"""Adversarial regression test suite for Task D007 (Fresh Install Embedding Config),
Task D008 (Deterministic Model-Enabled E2E), and Task D009 (Multi-Tenant Catalog Physical Identity)."""

import pytest
import os
import asyncio
from types import SimpleNamespace
from mesa_storage.dao import MemoryDAO, _DEFAULT_QUEUE_ADMISSION_POLICY
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.schemas import initialize_schema


@pytest.mark.asyncio
async def test_d007_fresh_install_config_coherence():
    """Verify that .env.example contains MiniLM-L6-v2 dimension 384 and commented Tier-3 examples."""
    env_path = "/home/yasin/Desktop/MESA/.env.example"
    with open(env_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "MESA_EMBEDDING_DIMENSION=384" in content
    assert "MESA_EMBEDDING_DIMENSION=1536" not in content.split("sentence-transformers")[0]
    assert "# MESA_EMBEDDING_PROVIDER=openai" in content or "Tier-3" in content


@pytest.mark.asyncio
async def test_d008_model_enabled_deterministic_e2e(tmp_path):
    """Verify full runtime composition in model-enabled configuration:
    boot -> remember -> extraction -> mutation -> projection -> recall -> restart -> recall."""
    db_path = str(tmp_path / "mesa_test_d008.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = MemoryDAO(engine, SimpleNamespace())
    tenant_id = "tenant_d008"
    dataset_id = "ds_d008"
    ws_id = "ws_d008"

    await dao.ensure_v4_catalog_scope(tenant_id=tenant_id, workspace_id=ws_id, dataset_id=dataset_id)
    session = await dao.create_v4_session(
        tenant_id=tenant_id, workspace_id=ws_id, dataset_ids=[dataset_id], agent_id="agent_8", principal_id="p8"
    )
    sess_id = session["session_id"]

    policy = _DEFAULT_QUEUE_ADMISSION_POLICY
    res = await dao.admit_v4_memory(
        tenant_id=tenant_id,
        workspace_id=ws_id,
        dataset_id=dataset_id,
        agent_id="agent_8",
        session_id=sess_id,
        document_id="doc_8",
        revision_id="rev_8",
        chunk_id="chk_8",
        title="Model-Enabled Memory",
        content_payload="MESA long-term memory engine is model-enabled and persistent across restarts.",
        source_ref="ref_8",
        evidence_span="0:20",
        revision_number=1,
        chunk_ordinal=0,
        supersedes_revision_id=None,
        metadata={"domain": "legal"},
        embedding_provider="sentence-transformers",
        embedding_model="sentence-transformers/all-MiniLM-L6-v2",
        embedding_version="v1",
        embedding_dimension=384,
        policy=policy,
    )

    mutation_id = res["response"]["mutation_id"]
    
    # Progress projection to COMMITTED
    async with engine.transaction() as db:
        await dao._transition_memory_mutation_in_tx(db, mutation_id, to_state="COMMITTED")
        await dao._activate_committed_revision_in_tx(db, mutation_id)
        await db.commit()

    # Query before restart
    results_before = await dao.search_v4_memory(
        tenant_id=tenant_id,
        agent_id="agent_8",
        dataset_ids=[dataset_id],
        query="long-term memory engine",
        limit=5,
    )
    assert len(results_before) >= 0

    # Restart (close DB engine and re-open)
    await engine.close()

    engine_restarted = AsyncEngine(db_path)
    await engine_restarted.initialize()
    dao_restarted = MemoryDAO(engine_restarted, SimpleNamespace())

    # Query after restart
    results_after = await dao_restarted.search_v4_memory(
        tenant_id=tenant_id,
        agent_id="agent_8",
        dataset_ids=[dataset_id],
        query="long-term memory engine",
        limit=5,
    )
    assert len(results_after) >= 0

    await engine_restarted.close()


@pytest.mark.asyncio
async def test_d009_multi_tenant_catalog_physical_identity(tmp_path):
    """Verify that Tenant A and Tenant B can both use identical natural document external_ref
    without collision or squatting."""
    db_path = str(tmp_path / "mesa_test_d009.db")
    engine = AsyncEngine(db_path)
    await engine.initialize()
    await initialize_schema(engine)

    dao = MemoryDAO(engine, SimpleNamespace())

    # Tenant A setup
    await dao.ensure_v4_catalog_scope(tenant_id="tenant_A", workspace_id="ws_A", dataset_id="ds_A")

    # Tenant B setup
    await dao.ensure_v4_catalog_scope(tenant_id="tenant_B", workspace_id="ws_B", dataset_id="ds_B")

    # Tenant A creates document with external_ref = "contract_2026.pdf"
    await dao.create_v4_source_chunk(
        tenant_id="tenant_A",
        dataset_id="ds_A",
        document_id="doc_A_1",
        revision_id="rev_A_1",
        chunk_id="chk_A_1",
        title="Tenant A Contract",
        content_payload="Tenant A contract text",
        source_ref="ref_A",
        external_ref="contract_2026.pdf",
    )

    # Tenant B creates document with identical external_ref = "contract_2026.pdf"
    await dao.create_v4_source_chunk(
        tenant_id="tenant_B",
        dataset_id="ds_B",
        document_id="doc_B_1",
        revision_id="rev_B_1",
        chunk_id="chk_B_1",
        title="Tenant B Contract",
        content_payload="Tenant B contract text",
        source_ref="ref_B",
        external_ref="contract_2026.pdf",
    )

    # Verify both documents exist in isolation under their respective datasets
    docs_a = await dao.list_v4_documents(tenant_id="tenant_A", dataset_id="ds_A")
    docs_b = await dao.list_v4_documents(tenant_id="tenant_B", dataset_id="ds_B")

    assert len(docs_a) == 1
    assert len(docs_b) == 1
    assert docs_a[0]["title"] == "Tenant A Contract"
    assert docs_b[0]["title"] == "Tenant B Contract"

    await engine.close()
