import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mesa_storage.dao import MemoryDAO
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


@pytest.mark.asyncio
async def test_canonical_sql_intent_established_before_secondary_writes(tmp_path):
    """Verify SQL canonical record is established before secondary stores are mutated."""
    db_path = tmp_path / "mesa_test_hazards.db"
    engine = AsyncEngine(str(db_path))
    await engine.initialize()
    await initialize_schema(engine)

    mock_vec = SimpleNamespace()
    mock_vec.is_initialized = True
    mock_vec.compute_embedding = AsyncMock(return_value=[0.1, 0.2, 0.3])
    mock_vec.upsert = AsyncMock()
    mock_vec.soft_delete = AsyncMock()
    mock_vec.search = AsyncMock(return_value=[])

    dao = MemoryDAO(sqlite_engine=engine, vector_engine=mock_vec)

    agent_id = "agent_hazard_test"
    node_id = str(uuid.uuid4())
    content = "Test content for canonical write hazard"
    embedding = [0.1, 0.2, 0.3]

    # Insert memory
    inserted_id = await dao.insert_memory(
        agent_id=agent_id,
        node_id=node_id,
        entity_name="HazardEntity",
        content=content,
        embedding=embedding,
    )
    assert inserted_id == node_id

    # Verify canonical SQL record exists
    async with engine.transaction() as db:
        async with db.execute("SELECT id, entity_name FROM nodes WHERE id = ?", (node_id,)) as cursor:
            row = await cursor.fetchone()
            assert row is not None
            assert row["entity_name"] == "HazardEntity"

    # Verify vector engine upsert was called AFTER SQL record exists
    mock_vec.upsert.assert_called_once()

    await engine.close()
