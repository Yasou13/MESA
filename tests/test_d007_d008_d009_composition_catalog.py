"""Adversarial regression test suite for Task D007 (Fresh Install Embedding Config),
Task D008 (Deterministic Model-Enabled E2E), and Task D009 (Multi-Tenant Catalog Physical Identity).
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from mesa_storage.dao import _DEFAULT_QUEUE_ADMISSION_POLICY, MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.mark.asyncio
async def test_d007_fresh_install_config_coherence():
    """Verify that .env.example contains MiniLM-L6-v2 dimension 384 and commented Tier-3 examples."""
    content = (Path(__file__).parents[1] / ".env.example").read_text(encoding="utf-8")

    assert "MESA_EMBEDDING_DIMENSION=384" in content
    assert (
        "MESA_EMBEDDING_DIMENSION=1536" not in content.split("sentence-transformers")[0]
    )
    assert "LLM_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2" in content
    for setting in (
        "MESA_TIER3_LLM_PROVIDER_A",
        "MESA_TIER3_LLM_MODEL_A",
        "MESA_TIER3_LLM_PROVIDER_B",
        "MESA_TIER3_LLM_MODEL_B",
    ):
        assert f"# {setting}=" in content


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
    await dao.ensure_v4_catalog_scope(
        tenant_id="tenant_A", workspace_id="default", dataset_id="main"
    )

    # Tenant B setup
    await dao.ensure_v4_catalog_scope(
        tenant_id="tenant_B", workspace_id="default", dataset_id="main"
    )

    # Tenant A creates document with external_ref = "contract_2026.pdf"
    await dao.create_v4_source_chunk(
        tenant_id="tenant_A",
        dataset_id="main",
        document_id="doc-1",
        revision_id="rev-1",
        chunk_id="chunk-1",
        title="Tenant A Contract",
        content_payload="Tenant A contract text",
        source_ref="ref_A",
        external_ref="contract_2026.pdf",
    )

    # Tenant B creates document with identical external_ref = "contract_2026.pdf"
    await dao.create_v4_source_chunk(
        tenant_id="tenant_B",
        dataset_id="main",
        document_id="doc-1",
        revision_id="rev-1",
        chunk_id="chunk-1",
        title="Tenant B Contract",
        content_payload="Tenant B contract text",
        source_ref="ref_B",
        external_ref="contract_2026.pdf",
    )

    # Verify both documents exist in isolation under their respective datasets
    docs_a = await dao.list_v4_documents(tenant_id="tenant_A", dataset_id="main")
    docs_b = await dao.list_v4_documents(tenant_id="tenant_B", dataset_id="main")

    assert len(docs_a) == 1
    assert len(docs_b) == 1
    assert docs_a[0]["title"] == "Tenant A Contract"
    assert docs_b[0]["title"] == "Tenant B Contract"
    assert docs_a[0]["document_id"] == docs_b[0]["document_id"] == "doc-1"
    async with engine.connection() as db:
        async with db.execute(
            "SELECT tenant_id, physical_id FROM v4_catalog_identities "
            "WHERE kind = 'document' AND external_id = 'doc-1' ORDER BY tenant_id"
        ) as cursor:
            physical = await cursor.fetchall()
    assert len(physical) == 2
    assert physical[0][1] != physical[1][1]

    session = await dao.create_v4_session(
        tenant_id="tenant_B",
        workspace_id="default",
        dataset_ids=["main"],
        agent_id="agent-b",
        principal_id="principal-b",
    )
    admitted = await dao.admit_v4_memory(
        tenant_id="tenant_B",
        workspace_id="default",
        dataset_id="main",
        agent_id="agent-b",
        session_id=session["session_id"],
        document_id="doc-1",
        revision_id="rev-1",
        chunk_id="chunk-1",
        title="Tenant B Contract",
        content_payload="Tenant B contract text",
        source_ref="ref_B",
        evidence_span="",
        revision_number=1,
        chunk_ordinal=0,
        supersedes_revision_id=None,
        metadata={},
        embedding_provider="local",
        embedding_model="sentence-transformers/all-MiniLM-L6-v2",
        embedding_version="v1",
        embedding_dimension=384,
        policy=_DEFAULT_QUEUE_ADMISSION_POLICY,
    )
    summary = await dao.get_mutation_summary(admitted["response"]["mutation_id"])
    assert summary is not None
    assert summary["workspace_id"] == "default"
    assert summary["dataset_id"] == "main"
    assert summary["document_id"] == "doc-1"
    assert summary["revision_id"] == "rev-1"
    assert summary["chunk_id"] == "chunk-1"

    await engine.close()
