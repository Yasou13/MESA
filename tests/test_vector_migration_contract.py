"""Vector retrieval must be explicit about degraded and migration-required states."""

from __future__ import annotations

import pytest
from mesa_storage.vector_engine import (
    EmbeddingMigrationRequiredError,
    VectorEngine,
    VectorSearchError,
)


@pytest.mark.asyncio
async def test_dimension_change_requires_reembedding_before_search(tmp_path) -> None:
    engine = VectorEngine(str(tmp_path / "vectors.lance"), max_workers=1)
    await engine.initialize()
    try:
        await engine.upsert("node-1", "agent-1", [1.0] * 8)

        with pytest.raises(EmbeddingMigrationRequiredError, match="re-embedding"):
            await engine.search([1.0] * 16, agent_id="agent-1")
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_lancedb_search_failure_is_not_reported_as_an_empty_result(tmp_path) -> None:
    engine = VectorEngine(str(tmp_path / "vectors.lance"), max_workers=1)
    await engine.initialize()
    try:
        await engine.upsert("node-1", "agent-1", [1.0] * 8)

        class BrokenTable:
            def search(self, *_args, **_kwargs):
                raise OSError("storage unavailable")

        engine._tables["mesa_vectors_8"] = BrokenTable()
        with pytest.raises(VectorSearchError, match="vector search failed"):
            await engine.search([1.0] * 8, agent_id="agent-1")
    finally:
        await engine.close()
