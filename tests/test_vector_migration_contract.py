"""Vector retrieval must be explicit about degraded and migration-required states."""

from __future__ import annotations

import pytest

from mesa_storage.vector_engine import (
    EmbeddingMigrationRequiredError,
    VectorEngine,
    VectorSearchError,
)


def test_dimension_change_requires_reembedding_before_search(tmp_path) -> None:
    engine = VectorEngine(str(tmp_path / "vectors.lance"), max_workers=1)
    engine._list_table_names = lambda: ["mesa_vectors_8"]  # type: ignore[method-assign]
    try:
        with pytest.raises(EmbeddingMigrationRequiredError, match="re-embedding"):
            engine._sync_search([1.0] * 16, 10, "agent-1", None, False)
    finally:
        engine._executor.shutdown(wait=False)


def test_lancedb_search_failure_is_not_reported_as_an_empty_result(tmp_path) -> None:
    engine = VectorEngine(str(tmp_path / "vectors.lance"), max_workers=1)
    engine._list_table_names = lambda: ["mesa_vectors_8"]  # type: ignore[method-assign]
    try:

        class BrokenTable:
            def search(self, *_args, **_kwargs):
                raise OSError("storage unavailable")

        engine._tables["mesa_vectors_8"] = BrokenTable()
        with pytest.raises(VectorSearchError, match="vector search failed"):
            engine._sync_search([1.0] * 8, 10, "agent-1", None, False)
    finally:
        engine._executor.shutdown(wait=False)


def test_vector_schema_inspection_failure_is_explicit(tmp_path) -> None:
    engine = VectorEngine(str(tmp_path / "vectors.lance"), max_workers=1)
    try:
        engine._list_table_names = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
            OSError("schema unavailable")
        )
        with pytest.raises(VectorSearchError, match="inspect vector index schema"):
            engine._sync_search([1.0] * 8, 10, "agent-1", None, False)
    finally:
        engine._executor.shutdown(wait=False)
