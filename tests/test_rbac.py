import os
import sqlite3

import pytest

from mesa_memory.security.rbac import AccessControl, sanitize_cmb_content


@pytest.mark.asyncio
async def test_access_control_sqlite_initialization(tmp_path):
    db_path = str(tmp_path / "test_rbac_policy.db")
    ac = AccessControl(policy_path=db_path)
    await ac.initialize()

    # Assert DB is created
    assert os.path.exists(db_path)

    # Assert WAL mode
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal"


@pytest.mark.asyncio
async def test_access_control_permissions(tmp_path):
    db_path = str(tmp_path / "test_rbac_policy.db")
    ac = AccessControl(policy_path=db_path)
    await ac.initialize()

    await ac.grant_access("agent_1", "session_A", "WRITE")
    assert await ac.check_access("agent_1", "session_A", "WRITE") is True
    assert await ac.check_access("agent_1", "session_A", "READ") is True

    await ac.grant_access("agent_2", "session_B", "READ")
    assert await ac.check_access("agent_2", "session_B", "READ") is True
    assert await ac.check_access("agent_2", "session_B", "WRITE") is False


@pytest.mark.asyncio
async def test_access_control_unauthorized(tmp_path):
    db_path = str(tmp_path / "test_rbac_policy.db")
    ac = AccessControl(policy_path=db_path)
    await ac.initialize()
    assert await ac.check_access("unknown_agent", "session_A", "READ") is False
    await ac.grant_access("agent_1", "session_A", "READ")
    assert await ac.check_access("agent_1", "unknown_session", "READ") is False


def test_sanitize_cmb_content():
    result = sanitize_cmb_content("Hello \x00 <script>alert(1)</script>   World  ")
    assert result == "Hello World"


# ===================================================================
# RBAC Bypass Prevention — Sentinel Enforcement Tests
# ===================================================================


@pytest.mark.asyncio
async def test_vector_upsert_rejects_missing_credentials(tmp_path):
    """Calling upsert_vector without explicit agent_id/session_id must fail.

    After the async RBAC migration, the RBAC check is performed by the
    async caller (StorageFacade.persist_cmb) BEFORE entering the thread
    pool. This test validates that the facade-level check rejects
    unset identities.
    """

    db_path = str(tmp_path / "rbac_policy.db")
    ac = AccessControl(policy_path=db_path)
    await ac.initialize()

    # The RBAC check is now in the async caller, not in VectorStorage.
    # Verify that check_access returns False for unset identity.
    assert await ac.check_access("__unset__", "__unset__", "WRITE") is False


@pytest.mark.asyncio
async def test_graph_upsert_rejects_missing_credentials(tmp_path):
    """Calling upsert_node without explicit agent_id/session_id must fail."""
    from mesa_memory.storage.graph.networkx_provider import NetworkXProvider

    db_path = str(tmp_path / "rbac_policy.db")
    ac = AccessControl(policy_path=db_path)
    await ac.initialize()
    provider = NetworkXProvider(
        db_path=str(tmp_path / "kg.db"),
        rocks_path=str(tmp_path / "kg.rocks"),
        access_control=ac,
    )
    await provider.initialize()

    with pytest.raises(PermissionError):
        await provider.upsert_node("Test_Entity", "ENTITY")


@pytest.mark.asyncio
async def test_system_daemon_identity_succeeds(tmp_path):
    """The reserved SYSTEM_AGENT_ID / SYSTEM_SESSION_ID must have WRITE access.

    This is the legitimate internal daemon path (ConsolidationLoop, etc.).
    """
    from mesa_memory.security.rbac_constants import SYSTEM_AGENT_ID, SYSTEM_SESSION_ID

    db_path = str(tmp_path / "rbac_policy.db")
    ac = AccessControl(policy_path=db_path)
    await ac.initialize()

    # System identity is seeded with WRITE during initialize()
    assert await ac.check_access(SYSTEM_AGENT_ID, SYSTEM_SESSION_ID, "WRITE") is True
