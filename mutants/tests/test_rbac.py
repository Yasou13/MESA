import sqlite3
import os
import pytest
from mesa_memory.security.rbac import AccessControl, sanitize_cmb_content


def test_access_control_sqlite_initialization(tmp_path):
    db_path = str(tmp_path / "test_rbac_policy.db")
    _ac = AccessControl(policy_path=db_path)

    # Assert DB is created
    assert os.path.exists(db_path)

    # Assert WAL mode
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal"


def test_access_control_permissions(tmp_path):
    db_path = str(tmp_path / "test_rbac_policy.db")
    ac = AccessControl(policy_path=db_path)

    ac.grant_access("agent_1", "session_A", "WRITE")
    assert ac.check_access("agent_1", "session_A", "WRITE") is True
    assert ac.check_access("agent_1", "session_A", "READ") is True

    ac.grant_access("agent_2", "session_B", "READ")
    assert ac.check_access("agent_2", "session_B", "READ") is True
    assert ac.check_access("agent_2", "session_B", "WRITE") is False


def test_access_control_unauthorized(tmp_path):
    db_path = str(tmp_path / "test_rbac_policy.db")
    ac = AccessControl(policy_path=db_path)
    assert ac.check_access("unknown_agent", "session_A", "READ") is False
    ac.grant_access("agent_1", "session_A", "READ")
    assert ac.check_access("agent_1", "unknown_session", "READ") is False


def test_sanitize_cmb_content():
    result = sanitize_cmb_content("Hello \x00 <script>alert(1)</script>   World  ")
    assert result == "Hello World"


# ===================================================================
# RBAC Bypass Prevention — Sentinel Enforcement Tests
# ===================================================================


def test_vector_upsert_rejects_missing_credentials(tmp_path):
    """Calling upsert_vector without explicit agent_id/session_id must fail.

    Previously, the default ``agent_id="system"`` silently bypassed RBAC.
    After the sentinel patch, the default ``_UNSET_IDENTITY`` has no
    permissions in the policy DB, so the call must raise PermissionError.
    """
    from mesa_memory.storage.vector_index import VectorStorage

    db_path = str(tmp_path / "rbac_policy.db")
    ac = AccessControl(policy_path=db_path)
    vs = VectorStorage(uri=str(tmp_path / "vec.lance"), access_control=ac)

    with pytest.raises(PermissionError):
        vs.upsert_vector(
            cmb_id="test-bypass-001",
            embedding=[0.1] * 768,
            content_payload="test",
            source="test",
            # agent_id and session_id intentionally omitted — sentinel default
        )


@pytest.mark.asyncio
async def test_graph_upsert_rejects_missing_credentials(tmp_path):
    """Calling upsert_node without explicit agent_id/session_id must fail."""
    from mesa_memory.storage.graph.networkx_provider import NetworkXProvider

    db_path = str(tmp_path / "rbac_policy.db")
    ac = AccessControl(policy_path=db_path)
    provider = NetworkXProvider(
        db_path=str(tmp_path / "kg.db"),
        rocks_path=str(tmp_path / "kg.rocks"),
        access_control=ac,
    )
    await provider.initialize()

    with pytest.raises(PermissionError):
        await provider.upsert_node("Test_Entity", "ENTITY")


def test_system_daemon_identity_succeeds(tmp_path):
    """The reserved SYSTEM_AGENT_ID / SYSTEM_SESSION_ID must have WRITE access.

    This is the legitimate internal daemon path (ConsolidationLoop, etc.).
    """
    from unittest.mock import patch
    from mesa_memory.security.rbac_constants import SYSTEM_AGENT_ID, SYSTEM_SESSION_ID
    from mesa_memory.storage.vector_index import VectorStorage

    db_path = str(tmp_path / "rbac_policy.db")
    ac = AccessControl(policy_path=db_path)
    vs = VectorStorage(uri=str(tmp_path / "vec.lance"), access_control=ac)

    # Mock memory check — this test validates RBAC, not memory limits
    with patch.object(vs, "_check_memory_limit"):
        # Should NOT raise PermissionError — system identity is seeded with WRITE
        vs.upsert_vector(
            cmb_id="test-system-001",
            embedding=[0.1] * 768,
            content_payload="system write",
            source="consolidation",
            agent_id=SYSTEM_AGENT_ID,
            session_id=SYSTEM_SESSION_ID,
        )
