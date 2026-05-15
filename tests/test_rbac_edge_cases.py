"""
RBAC Edge-Case Tests.

Verifies AccessControl behavior under adversarial and concurrent conditions:
1. Concurrent grant + revoke via threading (SQLite is sync, not asyncio).
2. Revoke-then-access: immediate denial after revocation.
3. READ access strictly denies WRITE operations.
4. Permission escalation rejection.
5. Non-existent agent denial.
6. Invalid access level rejection.
"""

import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from mesa_memory.security.rbac import AccessControl

RBAC_TEST_DIR = "./storage_rbac_test_tmp"


@pytest.fixture(autouse=True)
def rbac_test_dir():
    os.makedirs(RBAC_TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(RBAC_TEST_DIR, ignore_errors=True)


@pytest.fixture
def ac():
    return AccessControl(policy_path=os.path.join(RBAC_TEST_DIR, "test_rbac.db"))


# --- Concurrent grant + revoke ---


class TestConcurrentAccess:
    def test_concurrent_grant_and_revoke(self, ac):
        """Concurrent grant/revoke must not corrupt the database."""
        agent_id = "agent_concurrent"
        session_id = "session_concurrent"
        errors = []

        def _grant():
            try:
                ac.grant_access(agent_id, session_id, "WRITE")
            except Exception as e:
                errors.append(f"grant: {e}")

        def _revoke():
            try:
                ac.revoke_access(agent_id, session_id)
            except Exception as e:
                errors.append(f"revoke: {e}")

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = []
            for i in range(50):
                if i % 2 == 0:
                    futures.append(pool.submit(_grant))
                else:
                    futures.append(pool.submit(_revoke))
            for f in as_completed(futures):
                f.result()

        assert not errors, f"Concurrent ops produced errors: {errors}"
        # State is deterministic: either granted or revoked
        result = ac.check_access(agent_id, session_id, "WRITE")
        assert isinstance(result, bool)

    def test_concurrent_multi_agent_grants(self, ac):
        """Multiple agents granted concurrently — no cross-contamination."""
        errors = []

        def _grant_agent(idx):
            try:
                ac.grant_access(f"agent_{idx}", f"session_{idx}", "WRITE")
            except Exception as e:
                errors.append(str(e))

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(_grant_agent, i) for i in range(50)]
            for f in as_completed(futures):
                f.result()

        assert not errors
        # Each agent should have its own permission
        for i in range(50):
            assert ac.check_access(f"agent_{i}", f"session_{i}", "WRITE") is True
            # And NOT have access to other sessions
            assert ac.check_access(f"agent_{i}", f"session_{i + 100}", "READ") is False


# --- Revoke-then-access ---


class TestRevokeImmediacy:
    def test_revoke_denies_immediately(self, ac):
        """Access revoked → immediate denial on next check."""
        ac.grant_access("agent_revoke", "session_revoke", "WRITE")
        assert ac.check_access("agent_revoke", "session_revoke", "WRITE") is True

        ac.revoke_access("agent_revoke", "session_revoke")
        assert ac.check_access("agent_revoke", "session_revoke", "WRITE") is False
        assert ac.check_access("agent_revoke", "session_revoke", "READ") is False

    def test_revoke_then_re_grant(self, ac):
        """Revoke then re-grant restores access."""
        ac.grant_access("agent_rg", "session_rg", "WRITE")
        ac.revoke_access("agent_rg", "session_rg")
        assert ac.check_access("agent_rg", "session_rg", "WRITE") is False

        ac.grant_access("agent_rg", "session_rg", "READ")
        assert ac.check_access("agent_rg", "session_rg", "READ") is True
        assert ac.check_access("agent_rg", "session_rg", "WRITE") is False

    def test_double_revoke_is_safe(self, ac):
        """Revoking non-existent permission is a no-op, not an error."""
        ac.revoke_access("agent_phantom", "session_phantom")
        assert ac.check_access("agent_phantom", "session_phantom", "READ") is False


# --- READ cannot WRITE ---


class TestAccessLevelEnforcement:
    def test_read_denies_write(self, ac):
        """READ-only access strictly denies WRITE operations."""
        ac.grant_access("agent_ro", "session_ro", "READ")
        assert ac.check_access("agent_ro", "session_ro", "READ") is True
        assert ac.check_access("agent_ro", "session_ro", "WRITE") is False

    def test_write_implies_read(self, ac):
        """WRITE access implicitly grants READ."""
        ac.grant_access("agent_rw", "session_rw", "WRITE")
        assert ac.check_access("agent_rw", "session_rw", "READ") is True
        assert ac.check_access("agent_rw", "session_rw", "WRITE") is True

    def test_downgrade_write_to_read(self, ac):
        """Re-granting with READ downgrades from WRITE."""
        ac.grant_access("agent_dg", "session_dg", "WRITE")
        assert ac.check_access("agent_dg", "session_dg", "WRITE") is True

        ac.grant_access("agent_dg", "session_dg", "READ")
        assert ac.check_access("agent_dg", "session_dg", "WRITE") is False
        assert ac.check_access("agent_dg", "session_dg", "READ") is True


# --- Edge cases ---


class TestEdgeCases:
    def test_nonexistent_agent_denied(self, ac):
        """Agent with no grants is denied for all levels."""
        assert ac.check_access("ghost_agent", "ghost_session", "READ") is False
        assert ac.check_access("ghost_agent", "ghost_session", "WRITE") is False

    def test_invalid_access_level_raises(self, ac):
        """Granting an invalid level (not READ/WRITE) raises ValueError."""
        with pytest.raises(ValueError, match="Invalid access level"):
            ac.grant_access("agent_inv", "session_inv", "ADMIN")

    def test_invalid_level_superuser_rejected(self, ac):
        """Attempt to grant SUPERUSER level is rejected."""
        with pytest.raises(ValueError):
            ac.grant_access("agent_su", "session_su", "SUPERUSER")

    def test_empty_strings_handled(self, ac):
        """Empty agent_id/session_id are valid SQLite keys — should not crash."""
        ac.grant_access("", "", "READ")
        assert ac.check_access("", "", "READ") is True
        ac.revoke_access("", "")
        assert ac.check_access("", "", "READ") is False

    def test_check_unknown_required_level(self, ac):
        """Checking an unknown required_level returns False."""
        ac.grant_access("agent_unk", "session_unk", "WRITE")
        assert ac.check_access("agent_unk", "session_unk", "ADMIN") is False

    def test_session_isolation(self, ac):
        """Permissions are session-scoped: agent with access to session A
        cannot access session B."""
        ac.grant_access("agent_iso", "session_A", "WRITE")
        assert ac.check_access("agent_iso", "session_A", "WRITE") is True
        assert ac.check_access("agent_iso", "session_B", "READ") is False
