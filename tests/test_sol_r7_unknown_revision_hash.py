"""Regression for truthful unknown revision hashes on repeated direct insertion."""

from types import SimpleNamespace
from typing import Any

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.mark.asyncio
async def test_direct_insert_allows_multiple_unknown_revision_hashes(
    tmp_path: Any,
) -> None:
    engine = AsyncEngine(str(tmp_path / "unknown-revision-hashes.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    dao = MemoryDAO(engine, SimpleNamespace())
    await dao.ensure_v4_catalog_scope(
        tenant_id="tenant",
        workspace_id="workspace",
        dataset_id="dataset",
    )
    await dao.create_v4_document(
        tenant_id="tenant",
        dataset_id="dataset",
        document_id="document",
        title="Document",
    )
    for number in (1, 2):
        await dao.create_v4_source_chunk(
            tenant_id="tenant",
            dataset_id="dataset",
            document_id="document",
            revision_id=f"revision-{number}",
            chunk_id=f"chunk-{number}",
            title="Document",
            content_payload=f"payload-{number}",
            source_ref=f"source-{number}",
            revision_number=number,
            finalize_revision=False,
        )

    revisions = await dao.list_v4_revisions(
        tenant_id="tenant",
        dataset_id="dataset",
        document_id="document",
    )
    assert [row["declared_content_hash"] for row in revisions] == [None, None]
    assert [row["content_hash"] for row in revisions] == [None, None]
    await engine.close()
