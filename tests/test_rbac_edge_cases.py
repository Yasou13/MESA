"""
RBAC Edge-Case Tests.

Verifies AccessControl behavior under adversarial and concurrent conditions:
1. Concurrent grant + revoke via asyncio.gather.
2. Revoke-then-access: immediate denial after revocation.
3. READ access strictly denies WRITE operations.
4. Permission escalation rejection.
5. Non-existent agent denial.
6. Invalid access level rejection.
"""

import asyncio
import os
import shutil

import pytest
import pytest_asyncio

from mesa_memory.security.rbac import AccessControl
from tests.conftest import make_test_storage_dir

RBAC_TEST_DIR = make_test_storage_dir("rbac_test")


@pytest.fixture(autouse=True)
def rbac_test_dir():
    os.makedirs(RBAC_TEST_DIR, exist_ok=True)
    yield
    shutil.rmtree(RBAC_TEST_DIR, ignore_errors=True)


@pytest_asyncio.fixture
async def ac():
    ctrl = AccessControl(policy_path=os.path.join(RBAC_TEST_DIR, "test_rbac.db"))
    await ctrl.initialize()
    return ctrl


# --- Concurrent grant + revoke ---


class TestConcurrentAccess:
    @pytest.mark.asyncio
    async def test_concurrent_grant_and_revoke(self, ac):
        """Concurrent grant/revoke must not corrupt the database."""
        agent_id = "agent_concurrent"
        session_id = "session_concurrent"
        errors: list[str] = []

        async def _grant():
            try:
                await ac.grant_access(agent_id, session_id, "WRITE")
            except Exception as e:
                errors.append(f"grant: {e}")

        async def _revoke():
            try:
                await ac.revoke_access(agent_id, session_id)
            except Exception as e:
                errors.append(f"revoke: {e}")

        tasks = []
        for i in range(50):
            if i % 2 == 0:
                tasks.append(_grant())
            else:
                tasks.append(_revoke())
        await asyncio.gather(*tasks)

        assert not errors, f"Concurrent ops produced errors: {errors}"
        # State is deterministic: either granted or revoked
        result = await ac.check_access(agent_id, session_id, "WRITE")
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_concurrent_multi_agent_grants(self, ac):
        """Multiple agents granted concurrently — no cross-contamination."""
        errors: list[str] = []

        async def _grant_agent(idx):
            try:
                await ac.grant_access(f"agent_{idx}", f"session_{idx}", "WRITE")
            except Exception as e:
                errors.append(str(e))

        await asyncio.gather(*[_grant_agent(i) for i in range(50)])

        assert not errors
        # Each agent should have its own permission
        for i in range(50):
            assert await ac.check_access(f"agent_{i}", f"session_{i}", "WRITE") is True
            # And NOT have access to other sessions
            assert (
                await ac.check_access(f"agent_{i}", f"session_{i + 100}", "READ")
                is False
            )


# --- Revoke-then-access ---


class TestRevokeImmediacy:
    @pytest.mark.asyncio
    async def test_revoke_denies_immediately(self, ac):
        """Access revoked → immediate denial on next check."""
        await ac.grant_access("agent_revoke", "session_revoke", "WRITE")
        assert await ac.check_access("agent_revoke", "session_revoke", "WRITE") is True

        await ac.revoke_access("agent_revoke", "session_revoke")
        assert await ac.check_access("agent_revoke", "session_revoke", "WRITE") is False
        assert await ac.check_access("agent_revoke", "session_revoke", "READ") is False

    @pytest.mark.asyncio
    async def test_revoke_then_re_grant(self, ac):
        """Revoke then re-grant restores access."""
        await ac.grant_access("agent_rg", "session_rg", "WRITE")
        await ac.revoke_access("agent_rg", "session_rg")
        assert await ac.check_access("agent_rg", "session_rg", "WRITE") is False

        await ac.grant_access("agent_rg", "session_rg", "READ")
        assert await ac.check_access("agent_rg", "session_rg", "READ") is True
        assert await ac.check_access("agent_rg", "session_rg", "WRITE") is False

    @pytest.mark.asyncio
    async def test_double_revoke_is_safe(self, ac):
        """Revoking non-existent permission is a no-op, not an error."""
        await ac.revoke_access("agent_phantom", "session_phantom")
        assert (
            await ac.check_access("agent_phantom", "session_phantom", "READ") is False
        )


# --- READ cannot WRITE ---


class TestAccessLevelEnforcement:
    @pytest.mark.asyncio
    async def test_read_denies_write(self, ac):
        """READ-only access strictly denies WRITE operations."""
        await ac.grant_access("agent_ro", "session_ro", "READ")
        assert await ac.check_access("agent_ro", "session_ro", "READ") is True
        assert await ac.check_access("agent_ro", "session_ro", "WRITE") is False

    @pytest.mark.asyncio
    async def test_write_implies_read(self, ac):
        """WRITE access implicitly grants READ."""
        await ac.grant_access("agent_rw", "session_rw", "WRITE")
        assert await ac.check_access("agent_rw", "session_rw", "READ") is True
        assert await ac.check_access("agent_rw", "session_rw", "WRITE") is True

    @pytest.mark.asyncio
    async def test_regranting_read_preserves_write(self, ac):
        """A lower re-grant must not downgrade WRITE access."""
        await ac.grant_access("agent_dg", "session_dg", "WRITE")
        assert await ac.check_access("agent_dg", "session_dg", "WRITE") is True

        await ac.grant_access("agent_dg", "session_dg", "READ")
        assert await ac.check_access("agent_dg", "session_dg", "WRITE") is True
        assert await ac.check_access("agent_dg", "session_dg", "READ") is True

    @pytest.mark.asyncio
    async def test_admin_regrant_with_read_preserves_admin(self, ac):
        """A lower re-grant must not downgrade ADMIN access."""
        await ac.grant_access("agent_admin", "session_admin", "ADMIN")
        await ac.grant_access("agent_admin", "session_admin", "READ")

        assert await ac.check_access("agent_admin", "session_admin", "ADMIN") is True
        assert await ac.check_access("agent_admin", "session_admin", "WRITE") is True
        assert await ac.check_access("agent_admin", "session_admin", "READ") is True

    @pytest.mark.asyncio
    async def test_regranting_same_level_is_idempotent(self, ac):
        """Repeating one grant preserves its effective permission."""
        await ac.grant_access("agent_repeat", "session_repeat", "WRITE")
        await ac.grant_access("agent_repeat", "session_repeat", "WRITE")

        assert await ac.check_access("agent_repeat", "session_repeat", "WRITE") is True


# --- Edge cases ---


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_nonexistent_agent_denied(self, ac):
        """Agent with no grants is denied for all levels."""
        assert await ac.check_access("ghost_agent", "ghost_session", "READ") is False
        assert await ac.check_access("ghost_agent", "ghost_session", "WRITE") is False

    @pytest.mark.asyncio
    async def test_admin_access_level_is_supported(self, ac):
        """ADMIN includes the lower READ and WRITE permissions."""
        await ac.grant_access("agent_admin", "session_admin", "ADMIN")
        assert await ac.check_access("agent_admin", "session_admin", "ADMIN") is True
        assert await ac.check_access("agent_admin", "session_admin", "WRITE") is True
        assert await ac.check_access("agent_admin", "session_admin", "READ") is True

    @pytest.mark.asyncio
    async def test_invalid_access_level_raises(self, ac):
        """Granting an unknown level raises ValueError."""
        with pytest.raises(ValueError, match="Invalid access level"):
            await ac.grant_access("agent_inv", "session_inv", "SUPERUSER")

    @pytest.mark.asyncio
    async def test_invalid_level_superuser_rejected(self, ac):
        """Attempt to grant SUPERUSER level is rejected."""
        with pytest.raises(ValueError):
            await ac.grant_access("agent_su", "session_su", "SUPERUSER")

    @pytest.mark.asyncio
    async def test_empty_strings_handled(self, ac):
        """Empty agent_id/session_id are valid SQLite keys — should not crash."""
        await ac.grant_access("", "", "READ")
        assert await ac.check_access("", "", "READ") is True
        await ac.revoke_access("", "")
        assert await ac.check_access("", "", "READ") is False

    @pytest.mark.asyncio
    async def test_write_does_not_satisfy_admin(self, ac):
        """WRITE remains insufficient when an operation explicitly requires ADMIN."""
        await ac.grant_access("agent_unk", "session_unk", "WRITE")
        assert await ac.check_access("agent_unk", "session_unk", "ADMIN") is False

    @pytest.mark.asyncio
    async def test_session_isolation(self, ac):
        """Permissions are session-scoped: agent with access to session A
        cannot access session B."""
        await ac.grant_access("agent_iso", "session_A", "WRITE")
        assert await ac.check_access("agent_iso", "session_A", "WRITE") is True
        assert await ac.check_access("agent_iso", "session_B", "READ") is False
