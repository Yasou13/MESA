# MESA v0.3.0 — MemoryDAO Unit Tests (Coverage Gap Filler)
"""
Coverage for DAO storage and validation paths.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid

import pytest

from mesa_storage.dao import MemoryDAO, _assert_valid_agent_id
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import VectorEngine

TEST_DIR = os.path.join(os.path.dirname(__file__), ".test_storage_tmp", "dao")


@pytest.fixture(autouse=True)
def _clean():
    os.makedirs(TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest.fixture
def dao_env():
    uid = uuid.uuid4().hex[:8]
    db = os.path.join(TEST_DIR, f"dao_{uid}.db")
    vec = os.path.join(TEST_DIR, f"vec_{uid}.lance")
    sql = AsyncEngine(db, max_connections=2)
    vec_eng = VectorEngine(vec, max_workers=1)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(sql.initialize())
    loop.run_until_complete(initialize_schema(sql))
    loop.run_until_complete(vec_eng.initialize())
    dao = MemoryDAO(sqlite_engine=sql, vector_engine=vec_eng)
    yield dao, sql, vec_eng, loop
    loop.run_until_complete(sql.close())
    loop.run_until_complete(vec_eng.close())
    loop.close()


VEC8 = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


# === Agent ID Validation ===
class TestAgentIdValidation:
    def test_empty_raises(self):
        with pytest.raises(ValueError):
            _assert_valid_agent_id("")

    def test_unset_raises(self):
        with pytest.raises(ValueError):
            _assert_valid_agent_id("__unset__")

    def test_system_raises(self):
        with pytest.raises(ValueError):
            _assert_valid_agent_id("__system__")

    def test_valid_passes(self):
        _assert_valid_agent_id("agent-1")  # no exception


# === Insert ===


# === Bulk Insert ===


# === Get Memories ===


# === Mark Consolidated ===


# === Edge Operations ===


# === Get Neighbors ===


# === FTS5 Edge Cases ===


# === Purge Edge Cases ===


# === Health Check ===
class TestHealthCheck:
    def test_returns_both_engines(self, dao_env):
        dao, _, _, loop = dao_env
        h = loop.run_until_complete(dao.health_check())
        assert "sqlite" in h
        assert "vector" in h


# === Properties ===
class TestDAOProperties:
    def test_vector_engine(self, dao_env):
        dao, _, vec, _ = dao_env
        assert dao.vector_engine is vec
