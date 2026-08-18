"""Comprehensive test suite for Round 6 Part A: RBAC Tenant Isolation & Schema Migration.

Verifies:
- Explicit schema authority (RBAC_SCHEMA_VERSION = 2)
- Historical unscoped schema migration preserving recoverable rows
- Migration failure safety (rollback and non-corrupt state)
- Migration idempotence / repeat initialization
- Tenant-scoped workspace roles (same principal, same workspace ID, different tenants)
- Tenant-scoped dataset roles and permissions (same principal, same dataset ID, different tenants)
- Tenant-local revoke isolation (revoke in tenant A does not affect tenant B)
- Admin CLI support for grant and revoke operations
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import aiosqlite
import pytest

from mesa_memory.security.admin_cli import main
from mesa_memory.security.rbac import RBAC_SCHEMA_VERSION, AccessControl


def _create_historical_unscoped_db(db_path: str) -> None:
    """Create the historical Round 5 RBAC schema with unscoped primary keys."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("""
            CREATE TABLE permissions (
                agent_id TEXT,
                session_id TEXT,
                access_level TEXT,
                PRIMARY KEY (agent_id, session_id)
            )
        """)
        conn.execute("""
            CREATE TABLE principal_agent_permissions (
                principal_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                permission TEXT NOT NULL,
                PRIMARY KEY (principal_id, agent_id, permission)
            )
        """)
        conn.execute("""
            CREATE TABLE principal_session_permissions (
                principal_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                access_level TEXT NOT NULL,
                PRIMARY KEY (principal_id, session_id)
            )
        """)
        conn.execute("""
            CREATE TABLE principal_tenant_roles (
                principal_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                role TEXT NOT NULL,
                PRIMARY KEY (principal_id, tenant_id)
            )
        """)
        conn.execute("""
            CREATE TABLE principal_workspace_roles (
                principal_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                role TEXT NOT NULL,
                PRIMARY KEY (principal_id, workspace_id)
            )
        """)
        conn.execute("""
            CREATE TABLE principal_dataset_roles (
                principal_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                dataset_id TEXT NOT NULL,
                role TEXT NOT NULL,
                PRIMARY KEY (principal_id, dataset_id)
            )
        """)
        conn.execute("""
            CREATE TABLE principal_dataset_permissions (
                principal_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                dataset_id TEXT NOT NULL,
                permission TEXT NOT NULL,
                PRIMARY KEY (principal_id, dataset_id, permission)
            )
        """)
        conn.execute("""
            CREATE TABLE principal_control_roles (
                principal_id TEXT PRIMARY KEY,
                role TEXT NOT NULL
            )
        """)
        conn.commit()


@pytest.mark.asyncio
async def test_fresh_database_has_v2_schema_and_version(tmp_path) -> None:
    """Fresh initialization must create v2 schema with version recorded."""
    policy_path = str(tmp_path / "fresh_rbac.db")
    ac = AccessControl(policy_path=policy_path)
    await ac.initialize()

    assert await ac.get_schema_version() == RBAC_SCHEMA_VERSION

    # Verify primary key constraints on fresh tables
    with sqlite3.connect(policy_path) as conn:
        # workspace roles PK
        ws_info = conn.execute(
            "PRAGMA table_info(principal_workspace_roles)"
        ).fetchall()
        ws_pks = {row[1] for row in ws_info if row[5] > 0}
        assert ws_pks == {"principal_id", "tenant_id", "workspace_id"}

        # dataset roles PK
        ds_info = conn.execute("PRAGMA table_info(principal_dataset_roles)").fetchall()
        ds_pks = {row[1] for row in ds_info if row[5] > 0}
        assert ds_pks == {"principal_id", "tenant_id", "workspace_id", "dataset_id"}

        # dataset permissions PK
        perm_info = conn.execute(
            "PRAGMA table_info(principal_dataset_permissions)"
        ).fetchall()
        perm_pks = {row[1] for row in perm_info if row[5] > 0}
        assert perm_pks == {"principal_id", "tenant_id", "dataset_id", "permission"}


@pytest.mark.asyncio
async def test_historical_unscoped_migration_preserves_recoverable_grants(
    tmp_path,
) -> None:
    """Migrating an existing unscoped database upgrades PKs and preserves rows."""
    policy_path = str(tmp_path / "historical_rbac.db")
    _create_historical_unscoped_db(policy_path)

    # Insert historical rows into old schema
    with sqlite3.connect(policy_path) as conn:
        conn.execute(
            "INSERT INTO principal_workspace_roles (principal_id, tenant_id, workspace_id, role) "
            "VALUES (?, ?, ?, ?)",
            ("alice", "tenant-1", "ws-1", "READER"),
        )
        conn.execute(
            "INSERT INTO principal_workspace_roles (principal_id, tenant_id, workspace_id, role) "
            "VALUES (?, ?, ?, ?)",
            ("bob", "tenant-2", "ws-2", "OWNER"),
        )
        conn.execute(
            "INSERT INTO principal_dataset_roles (principal_id, tenant_id, workspace_id, dataset_id, role) "
            "VALUES (?, ?, ?, ?, ?)",
            ("alice", "tenant-1", "ws-1", "ds-1", "WRITER"),
        )
        conn.execute(
            "INSERT INTO principal_dataset_permissions (principal_id, tenant_id, dataset_id, permission) "
            "VALUES (?, ?, ?, ?)",
            ("alice", "tenant-1", "ds-1", "PURGE"),
        )
        conn.commit()

    # Perform production migration via AccessControl.initialize()
    ac = AccessControl(policy_path=policy_path)
    await ac.initialize()

    assert await ac.get_schema_version() == RBAC_SCHEMA_VERSION

    # Verify preserved rows can be authorized in new schema
    assert await ac.check_scope_role(
        "alice",
        tenant_id="tenant-1",
        workspace_id="ws-1",
        dataset_id="ds-1",
        required_role="WRITER",
    )
    assert await ac.check_dataset_permission(
        "alice",
        tenant_id="tenant-1",
        dataset_id="ds-1",
        permission="PURGE",
    )
    assert await ac.check_scope_role(
        "bob",
        tenant_id="tenant-2",
        workspace_id="ws-2",
        dataset_id="ds-unrelated",
        required_role="OWNER",
    )


@pytest.mark.asyncio
async def test_migration_idempotence_and_repeat_initialization(tmp_path) -> None:
    """Repeated initialization must not corrupt, duplicate, or alter migrated data."""
    policy_path = str(tmp_path / "repeat_rbac.db")
    _create_historical_unscoped_db(policy_path)

    with sqlite3.connect(policy_path) as conn:
        conn.execute(
            "INSERT INTO principal_workspace_roles (principal_id, tenant_id, workspace_id, role) "
            "VALUES (?, ?, ?, ?)",
            ("user-1", "t1", "w1", "WRITER"),
        )
        conn.commit()

    ac = AccessControl(policy_path=policy_path)
    await ac.initialize()

    # Re-initialize multiple times
    ac2 = AccessControl(policy_path=policy_path)
    await ac2.initialize()
    await ac2.initialize()

    assert await ac2.get_schema_version() == RBAC_SCHEMA_VERSION

    with sqlite3.connect(policy_path) as conn:
        rows = conn.execute("SELECT * FROM principal_workspace_roles").fetchall()
        assert len(rows) == 1
        assert rows[0][:4] == ("user-1", "t1", "w1", "WRITER")

        versions = conn.execute("SELECT version FROM rbac_schema_version").fetchall()
        assert len(versions) == 1
        assert versions[0][0] == RBAC_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_migration_injected_failure_rolls_back(tmp_path) -> None:
    """If an error occurs during migration, the transaction must rollback cleanly."""
    policy_path = str(tmp_path / "failure_rbac.db")
    _create_historical_unscoped_db(policy_path)

    with sqlite3.connect(policy_path) as conn:
        conn.execute(
            "INSERT INTO principal_workspace_roles (principal_id, tenant_id, workspace_id, role) "
            "VALUES (?, ?, ?, ?)",
            ("survivor", "t-orig", "ws-orig", "READER"),
        )
        conn.commit()

    ac = AccessControl(policy_path=policy_path)

    # Patch a step in migration to fail
    orig_execute = aiosqlite.Connection._execute

    async def failing_execute(self, *args, **kwargs):
        sql = args[1] if len(args) > 1 else str(kwargs.get("sql", ""))
        if "DROP TABLE principal_dataset_roles" in sql:
            raise RuntimeError("Injected migration disk failure")
        return await orig_execute(self, *args, **kwargs)

    with patch.object(aiosqlite.Connection, "_execute", failing_execute):
        with pytest.raises(RuntimeError, match="Injected migration disk failure"):
            await ac.initialize()

    # Verify old database remains usable with original data intact
    with sqlite3.connect(policy_path) as conn:
        ws_info = conn.execute(
            "PRAGMA table_info(principal_workspace_roles)"
        ).fetchall()
        ws_pks = {row[1] for row in ws_info if row[5] > 0}
        # Still old PK
        assert ws_pks == {"principal_id", "workspace_id"}

        row = conn.execute("SELECT * FROM principal_workspace_roles").fetchone()
        assert row is not None
        assert row[0] == "survivor"


@pytest.mark.asyncio
async def test_cross_tenant_workspace_roles_coexistence(tmp_path) -> None:
    """Same principal + same workspace ID in Tenant A and Tenant B must coexist independently."""
    policy_path = str(tmp_path / "ws_isolation.db")
    ac = AccessControl(policy_path=policy_path)
    await ac.initialize()

    # Principal P in Tenant A -> READER on workspace "default"
    await ac.grant_scope_role(
        "principal_p",
        tenant_id="tenant_a",
        workspace_id="default",
        role="READER",
    )

    # Principal P in Tenant B -> OWNER on workspace "default"
    await ac.grant_scope_role(
        "principal_p",
        tenant_id="tenant_b",
        workspace_id="default",
        role="OWNER",
    )

    # Tenant A must see READER (not OWNER)
    assert await ac.check_scope_role(
        "principal_p",
        tenant_id="tenant_a",
        workspace_id="default",
        dataset_id="any_ds",
        required_role="READER",
    )
    assert not await ac.check_scope_role(
        "principal_p",
        tenant_id="tenant_a",
        workspace_id="default",
        dataset_id="any_ds",
        required_role="OWNER",
    )

    # Tenant B must see OWNER
    assert await ac.check_scope_role(
        "principal_p",
        tenant_id="tenant_b",
        workspace_id="default",
        dataset_id="any_ds",
        required_role="OWNER",
    )

    # Updating Tenant A to WRITER must not affect Tenant B's OWNER role
    await ac.grant_scope_role(
        "principal_p",
        tenant_id="tenant_a",
        workspace_id="default",
        role="WRITER",
    )
    assert await ac.check_scope_role(
        "principal_p",
        tenant_id="tenant_a",
        workspace_id="default",
        dataset_id="any_ds",
        required_role="WRITER",
    )
    assert await ac.check_scope_role(
        "principal_p",
        tenant_id="tenant_b",
        workspace_id="default",
        dataset_id="any_ds",
        required_role="OWNER",
    )


@pytest.mark.asyncio
async def test_cross_tenant_dataset_roles_coexistence(tmp_path) -> None:
    """Same principal + same dataset ID in Tenant A and Tenant B must coexist independently."""
    policy_path = str(tmp_path / "ds_isolation.db")
    ac = AccessControl(policy_path=policy_path)
    await ac.initialize()

    # Principal P in Tenant A -> READER on dataset "main" in workspace "default"
    await ac.grant_scope_role(
        "principal_p",
        tenant_id="tenant_a",
        workspace_id="default",
        dataset_id="main",
        role="READER",
    )

    # Principal P in Tenant B -> WRITER on dataset "main" in workspace "default"
    await ac.grant_scope_role(
        "principal_p",
        tenant_id="tenant_b",
        workspace_id="default",
        dataset_id="main",
        role="WRITER",
    )

    # Verify both exist and are distinct
    assert await ac.check_scope_role(
        "principal_p",
        tenant_id="tenant_a",
        workspace_id="default",
        dataset_id="main",
        required_role="READER",
    )
    assert not await ac.check_scope_role(
        "principal_p",
        tenant_id="tenant_a",
        workspace_id="default",
        dataset_id="main",
        required_role="WRITER",
    )

    assert await ac.check_scope_role(
        "principal_p",
        tenant_id="tenant_b",
        workspace_id="default",
        dataset_id="main",
        required_role="WRITER",
    )


@pytest.mark.asyncio
async def test_cross_tenant_dataset_permissions_coexistence(tmp_path) -> None:
    """Explicit dataset permissions must be isolated across tenants."""
    policy_path = str(tmp_path / "perm_isolation.db")
    ac = AccessControl(policy_path=policy_path)
    await ac.initialize()

    # Grant PURGE to Alice in Tenant A on dataset "main"
    await ac.grant_dataset_permission(
        "alice",
        tenant_id="tenant_a",
        dataset_id="main",
        permission="PURGE",
    )

    # Grant ROLLBACK to Alice in Tenant B on dataset "main"
    await ac.grant_dataset_permission(
        "alice",
        tenant_id="tenant_b",
        dataset_id="main",
        permission="ROLLBACK",
    )

    # Check Tenant A
    assert await ac.check_dataset_permission(
        "alice",
        tenant_id="tenant_a",
        dataset_id="main",
        permission="PURGE",
    )
    assert not await ac.check_dataset_permission(
        "alice",
        tenant_id="tenant_a",
        dataset_id="main",
        permission="ROLLBACK",
    )

    # Check Tenant B
    assert await ac.check_dataset_permission(
        "alice",
        tenant_id="tenant_b",
        dataset_id="main",
        permission="ROLLBACK",
    )
    assert not await ac.check_dataset_permission(
        "alice",
        tenant_id="tenant_b",
        dataset_id="main",
        permission="PURGE",
    )


@pytest.mark.asyncio
async def test_cross_tenant_revoke_isolation(tmp_path) -> None:
    """Revoking role or permission in Tenant A must NOT affect Tenant B."""
    policy_path = str(tmp_path / "revoke_isolation.db")
    ac = AccessControl(policy_path=policy_path)
    await ac.initialize()

    # Grant in both tenants
    await ac.grant_scope_role(
        "alice",
        tenant_id="tenant_a",
        workspace_id="default",
        dataset_id="main",
        role="READER",
    )
    await ac.grant_scope_role(
        "alice",
        tenant_id="tenant_b",
        workspace_id="default",
        dataset_id="main",
        role="OWNER",
    )
    await ac.grant_dataset_permission(
        "alice",
        tenant_id="tenant_a",
        dataset_id="main",
        permission="PURGE",
    )
    await ac.grant_dataset_permission(
        "alice",
        tenant_id="tenant_b",
        dataset_id="main",
        permission="PURGE",
    )

    # Revoke role in Tenant A
    revoked_role = await ac.revoke_scope_role(
        "alice",
        tenant_id="tenant_a",
        workspace_id="default",
        dataset_id="main",
    )
    assert revoked_role is True

    # Tenant A must now be denied
    assert not await ac.check_scope_role(
        "alice",
        tenant_id="tenant_a",
        workspace_id="default",
        dataset_id="main",
        required_role="READER",
    )

    # Tenant B must remain unaffected and still have OWNER
    assert await ac.check_scope_role(
        "alice",
        tenant_id="tenant_b",
        workspace_id="default",
        dataset_id="main",
        required_role="OWNER",
    )

    # Revoke permission in Tenant A
    revoked_perm = await ac.revoke_dataset_permission(
        "alice",
        tenant_id="tenant_a",
        dataset_id="main",
        permission="PURGE",
    )
    assert revoked_perm is True

    # Tenant A permission is gone
    assert not await ac.check_dataset_permission(
        "alice",
        tenant_id="tenant_a",
        dataset_id="main",
        permission="PURGE",
    )

    # Tenant B permission remains
    assert await ac.check_dataset_permission(
        "alice",
        tenant_id="tenant_b",
        dataset_id="main",
        permission="PURGE",
    )


def test_admin_cli_grant_and_revoke_tenant_isolation(tmp_path, capsys) -> None:
    """CLI grant and revoke commands must properly pass tenant context and isolate."""
    policy = tmp_path / "cli_rbac.sqlite"

    # Grant Tenant A role
    assert (
        main(
            [
                "--policy-db",
                str(policy),
                "grant-role",
                "--principal",
                "principal-x",
                "--tenant",
                "tenant-1",
                "--workspace",
                "ws-1",
                "--dataset",
                "ds-1",
                "--role",
                "READER",
            ]
        )
        == 0
    )
    assert "role-granted:principal-x:ds-1:READER" in capsys.readouterr().out

    # Grant Tenant B role
    assert (
        main(
            [
                "--policy-db",
                str(policy),
                "grant-role",
                "--principal",
                "principal-x",
                "--tenant",
                "tenant-2",
                "--workspace",
                "ws-1",
                "--dataset",
                "ds-1",
                "--role",
                "WRITER",
            ]
        )
        == 0
    )
    assert "role-granted:principal-x:ds-1:WRITER" in capsys.readouterr().out

    # Revoke Tenant A role
    assert (
        main(
            [
                "--policy-db",
                str(policy),
                "revoke-role",
                "--principal",
                "principal-x",
                "--tenant",
                "tenant-1",
                "--workspace",
                "ws-1",
                "--dataset",
                "ds-1",
            ]
        )
        == 0
    )
    assert "role-revoked:principal-x:ds-1:True" in capsys.readouterr().out

    # Grant and revoke dataset permission
    assert (
        main(
            [
                "--policy-db",
                str(policy),
                "grant-dataset-permission",
                "--principal",
                "principal-x",
                "--tenant",
                "tenant-2",
                "--dataset",
                "ds-1",
                "--permission",
                "ROLLBACK",
            ]
        )
        == 0
    )
    assert (
        "dataset-permission-granted:principal-x:ds-1:ROLLBACK"
        in capsys.readouterr().out
    )

    assert (
        main(
            [
                "--policy-db",
                str(policy),
                "revoke-dataset-permission",
                "--principal",
                "principal-x",
                "--tenant",
                "tenant-2",
                "--dataset",
                "ds-1",
                "--permission",
                "ROLLBACK",
            ]
        )
        == 0
    )
    assert (
        "dataset-permission-revoked:principal-x:ds-1:ROLLBACK:True"
        in capsys.readouterr().out
    )
