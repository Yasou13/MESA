import logging
import os
import re

import aiosqlite

from mesa_memory.security.rbac_constants import SYSTEM_AGENT_ID, SYSTEM_SESSION_ID

logger = logging.getLogger("MESA_Security")

_ACCESS_LEVELS = {"READ": 1, "WRITE": 2, "ADMIN": 3}


def _validate_access_level(level: str, *, field_name: str) -> None:
    """Validate one level from the READ < WRITE < ADMIN hierarchy."""
    if level not in _ACCESS_LEVELS:
        allowed = ", ".join(_ACCESS_LEVELS)
        raise ValueError(f"{field_name} must be one of: {allowed}")


def _access_level_satisfies(granted: str, required: str) -> bool:
    """Return whether a granted level includes the required level."""
    return _ACCESS_LEVELS.get(granted, 0) >= _ACCESS_LEVELS.get(required, 0)


# ---------------------------------------------------------------------------
# Advisory prompt injection patterns — logged for observability but NOT used
# to hard-block content.  MESA's primary injection defense is architectural:
# all user content is interpolated inside <CONTENT>…</CONTENT> sandbox tags
# in the LLM prompt templates (see consolidation/loop.py), so the model is
# explicitly instructed to treat the block as untrusted data.
# ---------------------------------------------------------------------------
INJECTION_PATTERNS = [
    r"(?i)ignore\s+(all\s+)?previous\s+(instructions|context|rules|prompts)",
    r"(?i)disregard\s+(all\s+)?(prior|previous|above)",
    r"(?i)forget\s+(all\s+)?(previous|prior|above|your)\s+(instructions|rules|context)",
    r"(?i)override\s+(safety|security|restrictions|guidelines|filters)",
    r"(?i)\bDAN\b.*\bmode\b",
    r"(?i)do\s+anything\s+now",
    r"(?i)jailbreak",
    r"(?i)reveal\s+(your|the|all)\s+(system|hidden|secret)\s+(prompt|instructions|rules)",
    r"(?i)\[INST\]|\[/INST\]|<<SYS>>|<\|im_start\|>",
]


class PromptInjectionError(ValueError):
    """Raised when prompt injection is detected in content."""

    pass


def detect_prompt_injection(content: str) -> bool:
    """Check content for known prompt injection patterns (advisory only).

    Returns True if any pattern matches.  Callers may log or flag the
    content but should NOT hard-block it — false positives are common
    in conversational agent systems.
    """
    return any(re.search(p, content) for p in INJECTION_PATTERNS)


RBAC_SCHEMA_VERSION = 2


class AccessControl:
    """Async RBAC policy engine backed by aiosqlite.

    All connection management, policy reads, and permission evaluations
    are strictly asynchronous.  The ``initialize()`` coroutine MUST be
    awaited before any other method is called.

    Usage::

        ac = AccessControl(policy_path="./storage/rbac_policy.db")
        await ac.initialize()

        await ac.grant_access("agent_1", "session_A", "WRITE")
        has_access = await ac.check_access("agent_1", "session_A", "READ")

        await ac.close()
    """

    def __init__(self, policy_path: str = "./storage/rbac_policy.db"):
        self.policy_path = policy_path
        self._initialized = False

    async def get_schema_version(self) -> int:
        """Return the current RBAC policy schema version."""
        async with aiosqlite.connect(self.policy_path) as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='rbac_schema_version'"
            ) as cursor:
                if await cursor.fetchone() is None:
                    return 1
            async with db.execute(
                "SELECT MAX(version) FROM rbac_schema_version"
            ) as cursor:
                row = await cursor.fetchone()
                if row and row[0] is not None:
                    return int(row[0])
                return 1

    async def initialize(self) -> None:
        """Create or migrate the policy database and seed the system daemon identity.

        This is idempotent — safe to call multiple times.
        """
        if self._initialized:
            return

        os.makedirs(os.path.dirname(os.path.abspath(self.policy_path)), exist_ok=True)
        async with aiosqlite.connect(self.policy_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS permissions (
                    agent_id TEXT,
                    session_id TEXT,
                    access_level TEXT,
                    PRIMARY KEY (agent_id, session_id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS principal_agent_permissions (
                    principal_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    permission TEXT NOT NULL,
                    PRIMARY KEY (principal_id, agent_id, permission)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS principal_session_permissions (
                    principal_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    access_level TEXT NOT NULL,
                    PRIMARY KEY (principal_id, session_id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS principal_tenant_roles (
                    principal_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    PRIMARY KEY (principal_id, tenant_id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS principal_control_roles (
                    principal_id TEXT PRIMARY KEY,
                    role TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS rbac_schema_version (
                    version INTEGER PRIMARY KEY,
                    migrated_at TEXT NOT NULL
                )
            """)

            # The recorded version is only authoritative when the scoped tables
            # actually have the corresponding shape.  A stale or tampered
            # marker must not make an old policy schema silently usable.
            async with db.execute(
                "SELECT MAX(version) FROM rbac_schema_version"
            ) as cursor:
                row = await cursor.fetchone()
                current_version = int(row[0]) if (row and row[0] is not None) else 0

            if current_version > RBAC_SCHEMA_VERSION:
                raise RuntimeError(
                    "RBAC policy schema version is newer than this runtime supports"
                )

            needs_migration = False
            for tbl, required_cols in (
                (
                    "principal_workspace_roles",
                    {"principal_id", "tenant_id", "workspace_id", "role"},
                ),
                (
                    "principal_dataset_roles",
                    {
                        "principal_id",
                        "tenant_id",
                        "workspace_id",
                        "dataset_id",
                        "role",
                    },
                ),
                (
                    "principal_dataset_permissions",
                    {"principal_id", "tenant_id", "dataset_id", "permission"},
                ),
            ):
                async with db.execute(
                    f"SELECT name FROM sqlite_master WHERE type='table' AND name='{tbl}'"
                ) as cursor:
                    if await cursor.fetchone() is not None:
                        async with db.execute(
                            f"PRAGMA table_info({tbl})"
                        ) as col_cursor:
                            columns = await col_cursor.fetchall()
                        names = {str(col[1]) for col in columns}
                        pk_cols = {str(col[1]) for col in columns if col[5] > 0}
                        if names != required_cols or pk_cols != required_cols - {
                            "role"
                        }:
                            needs_migration = True
                            break

            if needs_migration:
                await db.execute("BEGIN IMMEDIATE")
                try:
                    await db.execute("""
                        CREATE TABLE IF NOT EXISTS _v2_principal_workspace_roles (
                            principal_id TEXT NOT NULL,
                            tenant_id TEXT NOT NULL,
                            workspace_id TEXT NOT NULL,
                            role TEXT NOT NULL,
                            PRIMARY KEY (principal_id, tenant_id, workspace_id)
                        )
                    """)
                    await db.execute("""
                        CREATE TABLE IF NOT EXISTS _v2_principal_dataset_roles (
                            principal_id TEXT NOT NULL,
                            tenant_id TEXT NOT NULL,
                            workspace_id TEXT NOT NULL,
                            dataset_id TEXT NOT NULL,
                            role TEXT NOT NULL,
                            PRIMARY KEY (principal_id, tenant_id, workspace_id, dataset_id)
                        )
                    """)
                    await db.execute("""
                        CREATE TABLE IF NOT EXISTS _v2_principal_dataset_permissions (
                            principal_id TEXT NOT NULL,
                            tenant_id TEXT NOT NULL,
                            dataset_id TEXT NOT NULL,
                            permission TEXT NOT NULL,
                            PRIMARY KEY (principal_id, tenant_id, dataset_id, permission)
                        )
                    """)

                    # Historical primary keys are strict subsets of the v2
                    # keys, so a conflict here signals malformed input.
                    # Fail closed rather than silently discard a grant.
                    await db.execute("""
                        INSERT INTO _v2_principal_workspace_roles
                        (principal_id, tenant_id, workspace_id, role)
                        SELECT principal_id, tenant_id, workspace_id, role
                        FROM principal_workspace_roles
                    """)
                    await db.execute("""
                        INSERT INTO _v2_principal_dataset_roles
                        (principal_id, tenant_id, workspace_id, dataset_id, role)
                        SELECT principal_id, tenant_id, workspace_id, dataset_id, role
                        FROM principal_dataset_roles
                    """)
                    await db.execute("""
                        INSERT INTO _v2_principal_dataset_permissions
                        (principal_id, tenant_id, dataset_id, permission)
                        SELECT principal_id, tenant_id, dataset_id, permission
                        FROM principal_dataset_permissions
                    """)

                    for source, replacement in (
                        ("principal_workspace_roles", "_v2_principal_workspace_roles"),
                        ("principal_dataset_roles", "_v2_principal_dataset_roles"),
                        (
                            "principal_dataset_permissions",
                            "_v2_principal_dataset_permissions",
                        ),
                    ):
                        async with db.execute(
                            f"SELECT COUNT(*) FROM {source}"
                        ) as cursor:
                            source_count = int((await cursor.fetchone())[0])
                        async with db.execute(
                            f"SELECT COUNT(*) FROM {replacement}"
                        ) as cursor:
                            replacement_count = int((await cursor.fetchone())[0])
                        if source_count != replacement_count:
                            raise RuntimeError(
                                "RBAC migration validation failed: grant count mismatch"
                            )

                    # Atomic drop and replace
                    await db.execute("DROP TABLE principal_workspace_roles")
                    await db.execute(
                        "ALTER TABLE _v2_principal_workspace_roles RENAME TO principal_workspace_roles"
                    )
                    await db.execute("DROP TABLE principal_dataset_roles")
                    await db.execute(
                        "ALTER TABLE _v2_principal_dataset_roles RENAME TO principal_dataset_roles"
                    )
                    await db.execute("DROP TABLE principal_dataset_permissions")
                    await db.execute(
                        "ALTER TABLE _v2_principal_dataset_permissions RENAME TO principal_dataset_permissions"
                    )

                    await db.execute(
                        "INSERT INTO rbac_schema_version (version, migrated_at) "
                        "VALUES (?, CURRENT_TIMESTAMP) "
                        "ON CONFLICT(version) DO NOTHING",
                        (RBAC_SCHEMA_VERSION,),
                    )
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise
            else:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS principal_workspace_roles (
                        principal_id TEXT NOT NULL,
                        tenant_id TEXT NOT NULL,
                        workspace_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        PRIMARY KEY (principal_id, tenant_id, workspace_id)
                    )
                """)
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS principal_dataset_roles (
                        principal_id TEXT NOT NULL,
                        tenant_id TEXT NOT NULL,
                        workspace_id TEXT NOT NULL,
                        dataset_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        PRIMARY KEY (principal_id, tenant_id, workspace_id, dataset_id)
                    )
                """)
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS principal_dataset_permissions (
                        principal_id TEXT NOT NULL,
                        tenant_id TEXT NOT NULL,
                        dataset_id TEXT NOT NULL,
                        permission TEXT NOT NULL,
                        PRIMARY KEY (principal_id, tenant_id, dataset_id, permission)
                    )
                """)
                await db.execute(
                    "INSERT INTO rbac_schema_version (version, migrated_at) "
                    "VALUES (?, CURRENT_TIMESTAMP) "
                    "ON CONFLICT(version) DO NOTHING",
                    (RBAC_SCHEMA_VERSION,),
                )
                await db.commit()

            # Seed the reserved system daemon identity with WRITE access
            await db.execute(
                "INSERT OR IGNORE INTO permissions "
                "(agent_id, session_id, access_level) VALUES (?, ?, ?)",
                (SYSTEM_AGENT_ID, SYSTEM_SESSION_ID, "WRITE"),
            )
            await db.commit()

        self._initialized = True

    async def grant_control_role(self, principal_id: str, role: str = "ADMIN") -> None:
        """Grant a server-side role for the MCP control plane."""
        normalized = role.upper()
        if normalized != "ADMIN":
            raise ValueError("invalid control role")
        async with aiosqlite.connect(self.policy_path) as db:
            await db.execute(
                "INSERT INTO principal_control_roles (principal_id, role) VALUES (?, ?) "
                "ON CONFLICT(principal_id) DO UPDATE SET role = excluded.role",
                (principal_id, normalized),
            )
            await db.commit()

    async def check_control_role(self, principal_id: str, required_role: str) -> bool:
        """Return whether a principal has the requested control-plane role."""
        if required_role.upper() != "ADMIN":
            return False
        async with aiosqlite.connect(self.policy_path) as db:
            async with db.execute(
                "SELECT role FROM principal_control_roles WHERE principal_id = ?",
                (principal_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return row is not None and str(row[0]).upper() == "ADMIN"

    async def grant_scope_role(
        self,
        principal_id: str,
        *,
        tenant_id: str,
        role: str,
        workspace_id: str | None = None,
        dataset_id: str | None = None,
    ) -> None:
        """Grant OWNER/WRITER/READER at exactly one catalog scope."""
        normalized = role.upper()
        if normalized not in {"OWNER", "WRITER", "READER"}:
            raise ValueError("invalid catalog role")
        params: tuple[str, ...]
        if dataset_id:
            if not workspace_id:
                raise ValueError("dataset role requires workspace")
            statement = (
                "INSERT INTO principal_dataset_roles "
                "(principal_id, tenant_id, workspace_id, dataset_id, role) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(principal_id, tenant_id, workspace_id, dataset_id) "
                "DO UPDATE SET role = excluded.role"
            )
            params = (
                principal_id,
                tenant_id,
                workspace_id,
                dataset_id,
                normalized,
            )
        elif workspace_id:
            statement = (
                "INSERT INTO principal_workspace_roles "
                "(principal_id, tenant_id, workspace_id, role) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(principal_id, tenant_id, workspace_id) "
                "DO UPDATE SET role = excluded.role"
            )
            params = (principal_id, tenant_id, workspace_id, normalized)
        else:
            statement = (
                "INSERT INTO principal_tenant_roles "
                "(principal_id, tenant_id, role) VALUES (?, ?, ?) "
                "ON CONFLICT(principal_id, tenant_id) "
                "DO UPDATE SET role = excluded.role"
            )
            params = (principal_id, tenant_id, normalized)
        async with aiosqlite.connect(self.policy_path) as db:
            await db.execute(statement, params)
            await db.commit()

    async def revoke_scope_role(
        self,
        principal_id: str,
        *,
        tenant_id: str,
        workspace_id: str | None = None,
        dataset_id: str | None = None,
    ) -> bool:
        """Revoke role at specified catalog scope. Returns True if a role was revoked."""
        if dataset_id:
            if not workspace_id:
                raise ValueError("dataset role revocation requires workspace")
            statement = (
                "DELETE FROM principal_dataset_roles "
                "WHERE principal_id = ? AND tenant_id = ? "
                "AND workspace_id = ? AND dataset_id = ?"
            )
            params = (principal_id, tenant_id, workspace_id, dataset_id)
        elif workspace_id:
            statement = (
                "DELETE FROM principal_workspace_roles "
                "WHERE principal_id = ? AND tenant_id = ? AND workspace_id = ?"
            )
            params = (principal_id, tenant_id, workspace_id)
        else:
            statement = (
                "DELETE FROM principal_tenant_roles "
                "WHERE principal_id = ? AND tenant_id = ?"
            )
            params = (principal_id, tenant_id)
        async with aiosqlite.connect(self.policy_path) as db:
            cursor = await db.execute(statement, params)
            await db.commit()
            return bool(cursor.rowcount > 0)

    async def check_scope_role(
        self,
        principal_id: str,
        *,
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
        required_role: str,
    ) -> bool:
        """Apply inherited tenant → workspace → dataset role precedence."""
        levels = {"READER": 1, "WRITER": 2, "OWNER": 3}
        required = levels.get(required_role.upper())
        if required is None:
            raise ValueError("invalid required catalog role")
        async with aiosqlite.connect(self.policy_path) as db:
            queries = (
                (
                    "SELECT role FROM principal_tenant_roles "
                    "WHERE principal_id = ? AND tenant_id = ?",
                    (principal_id, tenant_id),
                ),
                (
                    "SELECT role FROM principal_workspace_roles "
                    "WHERE principal_id = ? AND tenant_id = ? AND workspace_id = ?",
                    (principal_id, tenant_id, workspace_id),
                ),
                (
                    "SELECT role FROM principal_dataset_roles "
                    "WHERE principal_id = ? AND tenant_id = ? "
                    "AND workspace_id = ? AND dataset_id = ?",
                    (principal_id, tenant_id, workspace_id, dataset_id),
                ),
            )
            granted = 0
            for statement, params in queries:
                async with db.execute(statement, params) as cursor:
                    row = await cursor.fetchone()
                if row:
                    granted = max(granted, levels.get(str(row[0]).upper(), 0))
        return granted >= required

    async def grant_dataset_permission(
        self,
        principal_id: str,
        *,
        tenant_id: str,
        dataset_id: str,
        permission: str,
    ) -> None:
        normalized = permission.upper()
        if normalized not in {"PURGE", "ROLLBACK"}:
            raise ValueError("invalid dataset permission")
        async with aiosqlite.connect(self.policy_path) as db:
            await db.execute(
                "INSERT INTO principal_dataset_permissions "
                "(principal_id, tenant_id, dataset_id, permission) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(principal_id, tenant_id, dataset_id, permission) DO NOTHING",
                (principal_id, tenant_id, dataset_id, normalized),
            )
            await db.commit()

    async def revoke_dataset_permission(
        self,
        principal_id: str,
        *,
        tenant_id: str,
        dataset_id: str,
        permission: str,
    ) -> bool:
        """Revoke explicit dataset permission. Returns True if permission was revoked."""
        normalized = permission.upper()
        if normalized not in {"PURGE", "ROLLBACK"}:
            raise ValueError("invalid dataset permission")
        async with aiosqlite.connect(self.policy_path) as db:
            cursor = await db.execute(
                "DELETE FROM principal_dataset_permissions "
                "WHERE principal_id = ? AND tenant_id = ? "
                "AND dataset_id = ? AND permission = ?",
                (principal_id, tenant_id, dataset_id, normalized),
            )
            await db.commit()
            return bool(cursor.rowcount > 0)

    async def check_dataset_permission(
        self,
        principal_id: str,
        *,
        tenant_id: str,
        dataset_id: str,
        permission: str,
    ) -> bool:
        async with aiosqlite.connect(self.policy_path) as db:
            async with db.execute(
                "SELECT 1 FROM principal_dataset_permissions "
                "WHERE principal_id = ? AND tenant_id = ? "
                "AND dataset_id = ? AND permission = ?",
                (principal_id, tenant_id, dataset_id, permission.upper()),
            ) as cursor:
                return await cursor.fetchone() is not None

    async def close(self) -> None:
        """Mark the controller as closed.

        Each operation opens and closes its own connection via
        ``async with aiosqlite.connect()``, so there is no persistent
        connection to tear down.  This method exists for lifecycle
        symmetry with other MESA engines.
        """
        self._initialized = False

    async def grant_principal_permission(
        self, principal_id: str, agent_id: str, permission: str
    ) -> None:
        """Provision one explicit server-side principal-to-agent permission."""
        if permission not in {
            "READ",
            "WRITE",
            "SESSION_CREATE",
            "SESSION_READ",
            "SESSION_UPDATE",
            "STATUS_READ",
            "PURGE",
            "ADMIN",
        }:
            raise ValueError(f"Invalid principal permission: {permission}")
        async with aiosqlite.connect(self.policy_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO principal_agent_permissions "
                "(principal_id, agent_id, permission) VALUES (?, ?, ?)",
                (principal_id, agent_id, permission),
            )
            await db.commit()

    async def check_principal_permission(
        self, principal_id: str, agent_id: str, permission: str
    ) -> bool:
        """Return whether an explicit server-side principal mapping permits action."""
        async with aiosqlite.connect(self.policy_path) as db:
            if permission in _ACCESS_LEVELS:
                async with db.execute(
                    "SELECT permission FROM principal_agent_permissions "
                    "WHERE principal_id = ? AND agent_id = ? "
                    "AND permission IN ('READ', 'WRITE', 'ADMIN')",
                    (principal_id, agent_id),
                ) as cursor:
                    rows = await cursor.fetchall()
                return any(_access_level_satisfies(row[0], permission) for row in rows)
            async with db.execute(
                "SELECT 1 FROM principal_agent_permissions "
                "WHERE principal_id = ? AND agent_id = ? AND permission = ?",
                (principal_id, agent_id, permission),
            ) as cursor:
                return await cursor.fetchone() is not None

    async def grant_principal_session_access(
        self, principal_id: str, agent_id: str, session_id: str, level: str
    ) -> None:
        """Persist a server-side principal ownership/access binding for a session."""
        _validate_access_level(level, field_name="session access level")
        async with aiosqlite.connect(self.policy_path) as db:
            await db.execute(
                "INSERT INTO principal_session_permissions "
                "(principal_id, agent_id, session_id, access_level) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(principal_id, session_id) DO UPDATE SET "
                "agent_id = excluded.agent_id, "
                "access_level = CASE WHEN "
                "CASE principal_session_permissions.access_level "
                "WHEN 'ADMIN' THEN 3 WHEN 'WRITE' THEN 2 WHEN 'READ' THEN 1 ELSE 0 END "
                ">= CASE excluded.access_level "
                "WHEN 'ADMIN' THEN 3 WHEN 'WRITE' THEN 2 WHEN 'READ' THEN 1 ELSE 0 END "
                "THEN principal_session_permissions.access_level ELSE excluded.access_level END",
                (principal_id, agent_id, session_id, level),
            )
            await db.commit()

    async def check_principal_session_access(
        self, principal_id: str, agent_id: str, session_id: str, required_level: str
    ) -> bool:
        """Check a trusted session binding; client agent IDs never create authority."""
        _validate_access_level(
            required_level, field_name="required session access level"
        )
        async with aiosqlite.connect(self.policy_path) as db:
            async with db.execute(
                "SELECT access_level FROM principal_session_permissions "
                "WHERE principal_id = ? AND agent_id = ? AND session_id = ?",
                (principal_id, agent_id, session_id),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return False
        return _access_level_satisfies(row[0], required_level)

    async def grant_access(self, agent_id: str, session_id: str, level: str) -> None:
        """Grant access without lowering an existing agent/session permission.

        Permissions are stored as one level per ``(agent_id, session_id)``.
        The upsert preserves the higher of the existing and requested levels.
        """
        _validate_access_level(level, field_name="Invalid access level")
        async with aiosqlite.connect(self.policy_path) as db:
            await db.execute(
                "INSERT INTO permissions (agent_id, session_id, access_level) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(agent_id, session_id) DO UPDATE SET "
                "access_level = CASE WHEN "
                "CASE permissions.access_level "
                "WHEN 'ADMIN' THEN 3 WHEN 'WRITE' THEN 2 WHEN 'READ' THEN 1 ELSE 0 END "
                ">= CASE excluded.access_level "
                "WHEN 'ADMIN' THEN 3 WHEN 'WRITE' THEN 2 WHEN 'READ' THEN 1 ELSE 0 END "
                "THEN permissions.access_level ELSE excluded.access_level END",
                (agent_id, session_id, level),
            )
            await db.commit()

    async def revoke_access(self, agent_id: str, session_id: str) -> None:
        """Revoke all access for an agent/session pair."""
        async with aiosqlite.connect(self.policy_path) as db:
            await db.execute(
                "DELETE FROM permissions WHERE agent_id = ? AND session_id = ?",
                (agent_id, session_id),
            )
            await db.commit()

    async def check_access(
        self, agent_id: str, session_id: str, required_level: str
    ) -> bool:
        """Check whether an agent/session has the required access level.

        Returns True if the granted level satisfies the requirement:
        - READ is satisfied by READ, WRITE, or ADMIN.
        - WRITE is satisfied by WRITE or ADMIN.
        - ADMIN is satisfied only by ADMIN.
        """
        if required_level not in _ACCESS_LEVELS:
            return False
        async with aiosqlite.connect(self.policy_path) as db:
            async with db.execute(
                "SELECT access_level FROM permissions "
                "WHERE agent_id = ? AND session_id = ?",
                (agent_id, session_id),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return False
                return _access_level_satisfies(row[0], required_level)

    # -- Async context manager for lifecycle symmetry -----------------------

    async def __aenter__(self) -> "AccessControl":
        await self.initialize()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()


def sanitize_cmb_content(content: str) -> str:
    """Standard input normalization for CMB content payloads.

    Performs the following transformations:
    1. Strips null bytes (binary safety).
    2. Strips ANSI escape sequences (terminal injection).
    3. Strips dangerous HTML tags AND their content (script, style, iframe, etc.).
    4. Strips remaining HTML tags (preserves text content).
    5. Neutralizes common shell metacharacters (backticks, $()).
    6. Normalizes whitespace to single spaces.
    7. Advisory-only prompt injection logging (no hard block).
    """
    # 1. Null bytes
    content = content.replace("\x00", "")

    # 2. ANSI escape sequences
    content = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", content)

    # 3. Strip dangerous tags AND their content (script, style, etc.)
    content = re.sub(
        r"<(script|style|iframe|object|embed)[^>]*>.*?</\1>",
        "",
        content,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # 4. Strip remaining HTML tags
    content = re.sub(r"<[^>]*>", "", content)

    # 5. Neutralize shell metacharacters
    content = content.replace("`", "'")
    content = re.sub(r"\$\(", "(", content)

    # 6. Normalize whitespace
    content = " ".join(content.split())

    # 7. Advisory logging — flag but do not block
    if detect_prompt_injection(content):
        logger.info(
            "PROMPT_INJECTION_ADVISORY: Content matched injection heuristic "
            "(advisory only — content passed through)"
        )

    return content
