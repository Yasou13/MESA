"""Fast fault-injection coverage for V4 vector failure semantics."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.vector_engine import (
    EmbeddingMigrationRequiredError,
    SemanticRuntimeDisabledError,
    VectorSearchError,
)


class _Cursor:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    async def __aenter__(self) -> "_Cursor":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def fetchall(self) -> list[object]:
        return self._rows


class _Connection:
    def execute(self, query: str, _params: object) -> _Cursor:
        if "artifact_registry" in query:
            return _Cursor(
                [
                    ("ENTITY", "entity-1"),
                    ("ASSERTION_VECTOR", "assertion-1"),
                ]
            )
        return _Cursor([])


class _Engine:
    @asynccontextmanager
    async def connection(self):  # type: ignore[no-untyped-def]
        yield _Connection()


class _Catalog:
    async def resolve_id_in_tx(self, *_args: object, **_kwargs: object) -> str:
        return "dataset-physical-1"


def _dao(
    *,
    compute_error: Exception | None = None,
    search_error: Exception | None = None,
) -> MemoryDAO:
    vector = AsyncMock()
    vector.compute_query_embedding = AsyncMock(
        side_effect=compute_error,
        return_value=[0.1, 0.2],
    )
    vector.search = AsyncMock(side_effect=search_error, return_value=[])
    dao = MemoryDAO(_Engine(), vector)  # type: ignore[arg-type]
    dao._catalog = _Catalog()  # type: ignore[assignment]
    return dao


async def _search(dao: MemoryDAO) -> list[dict]:
    return await dao.search_v4_memory(
        tenant_id="tenant-1",
        agent_id="agent-1",
        dataset_ids=["dataset-1"],
        query="known entity",
    )


@pytest.mark.asyncio
async def test_intentional_semantic_runtime_disabled_degrades() -> None:
    dao = _dao(compute_error=SemanticRuntimeDisabledError("disabled"))
    assert await _search(dao) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        VectorSearchError("LanceDB unavailable"),
        VectorSearchError("could not inspect vector index schema"),
        EmbeddingMigrationRequiredError("re-embedding required"),
    ],
)
async def test_vector_operational_failures_propagate(failure: Exception) -> None:
    dao = _dao(search_error=failure)
    with pytest.raises(type(failure), match=str(failure)):
        await _search(dao)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        OSError("embedding provider unavailable"),
        RuntimeError("unexpected embedding runtime failure"),
    ],
)
async def test_query_embedding_failures_propagate(failure: Exception) -> None:
    dao = _dao(compute_error=failure)
    with pytest.raises(type(failure), match=str(failure)):
        await _search(dao)
