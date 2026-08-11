import uuid
from types import SimpleNamespace

import pytest

from mesa_storage.dao import MemoryDAO, QueueRecordTooLargeError
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.mark.asyncio
async def test_shared_write_admission_policy(tmp_path):
    """Verify common write admission invariants across all mutation entry points."""
    db_path = tmp_path / "mesa_test_write_admission.db"
    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    await initialize_schema(engine)

    mock_vec = SimpleNamespace()
    mock_vec.is_initialized = True
    mock_graph = SimpleNamespace()

    dao = MemoryDAO(sqlite_engine=engine, vector_engine=mock_vec, graph_provider=mock_graph)

    tenant_id = "tenant_adm"
    agent_id = "agent_adm"
    workspace_id = "ws_adm"
    dataset_id = "dataset_adm"
    doc_id = f"doc_{uuid.uuid4().hex[:8]}"
    rev_id = f"rev_{uuid.uuid4().hex[:8]}"

    await dao.create_v4_workspace(tenant_id=tenant_id, workspace_id=workspace_id, workspace_name="WS Adm")
    await dao.ensure_v4_catalog_scope(tenant_id=tenant_id, workspace_id=workspace_id, dataset_id=dataset_id)
    await dao.create_v4_document(tenant_id=tenant_id, dataset_id=dataset_id, title="Doc Adm", document_id=doc_id)
    await dao.create_v4_revision(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=doc_id,
        revision_id=rev_id,
        revision_number=1,
        content_hash="a" * 64,
    )

    policy = SimpleNamespace(
        queue_max_single_record_bytes=100,
        queue_max_total_bytes=100000,
        queue_max_records=1000,
    )

    # 1. Invalid agent_id raises ValueError
    with pytest.raises(ValueError, match="agent_id must be a non-empty"):
        await dao.admit_v4_memory(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            dataset_id=dataset_id,
            session_id="sess_1",
            agent_id="",  # Empty agent_id!
            document_id=doc_id,
            revision_id=rev_id,
            chunk_id="c1",
            title="Title",
            content_payload="Payload",
            source_ref="ref_1",
            evidence_span="span",
            revision_number=1,
            chunk_ordinal=1,
            supersedes_revision_id=None,
            metadata={},
            embedding_provider="st",
            embedding_model="model",
            embedding_version="1.0",
            embedding_dimension=384,
            policy=policy,
        )

    # 2. Payload size exceeding single record byte limit raises QueueRecordTooLargeError
    huge_payload = "X" * 500  # Exceeds limit=100
    with pytest.raises(QueueRecordTooLargeError):
        await dao.admit_v4_memory(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            dataset_id=dataset_id,
            session_id="sess_1",
            agent_id=agent_id,
            document_id=doc_id,
            revision_id=rev_id,
            chunk_id="c1",
            title="Title",
            content_payload=huge_payload,
            source_ref="ref_1",
            evidence_span="span",
            revision_number=1,
            chunk_ordinal=1,
            supersedes_revision_id=None,
            metadata={},
            embedding_provider="st",
            embedding_model="model",
            embedding_version="1.0",
            embedding_dimension=384,
            policy=policy,
        )

    # 3. Idempotency key supplied without payload hash raises ValueError
    with pytest.raises(ValueError, match="idempotency key and payload hash must be supplied together"):
        await dao.admit_v4_memory(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            dataset_id=dataset_id,
            session_id="sess_1",
            agent_id=agent_id,
            document_id=doc_id,
            revision_id=rev_id,
            chunk_id="c1",
            title="Title",
            content_payload="Small payload",
            source_ref="ref_1",
            evidence_span="span",
            revision_number=1,
            chunk_ordinal=1,
            supersedes_revision_id=None,
            metadata={},
            embedding_provider="st",
            embedding_model="model",
            embedding_version="1.0",
            embedding_dimension=384,
            policy=SimpleNamespace(
                queue_max_single_record_bytes=100000,
                queue_max_total_bytes=1000000,
                queue_max_records=1000,
            ),
            idempotency_key="idem_key_only",
            payload_hash=None,
        )

    await engine.close()
