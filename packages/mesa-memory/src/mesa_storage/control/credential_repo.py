"""Durable, binding-scoped bearer credentials for HTTP MCP clients."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from mesa_storage.sqlite_engine import AsyncEngine

DEFAULT_CREDENTIAL_TTL_SECONDS = 90 * 24 * 60 * 60


class CredentialRepository:
    """Issue and verify high-entropy credentials without retaining their secret."""

    def __init__(self, sqlite_engine: AsyncEngine):
        self._sql = sqlite_engine

    async def issue(
        self,
        client_id: str,
        binding_id: str,
        *,
        token_kind: str = "codex",
        expires_in_seconds: int = DEFAULT_CREDENTIAL_TTL_SECONDS,
    ) -> tuple[dict[str, Any], str]:
        credential_id = f"cred_{uuid.uuid4().hex}"
        secret = secrets.token_urlsafe(32)
        if token_kind not in {"codex", "antigravity"}:
            raise ValueError("unsupported credential token kind")
        if expires_in_seconds <= 0:
            raise ValueError("expires_in_seconds must be positive")
        token = f"mesa_{token_kind}_{credential_id}_{secret}"
        now = datetime.now(timezone.utc).isoformat()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
        ).isoformat()
        record = {
            "credential_id": credential_id,
            "client_id": client_id,
            "binding_id": binding_id,
            "token_hash": _token_hash(token),
            "token_prefix": token[:24],
            "status": "ACTIVE",
            "created_at": now,
            "expires_at": expires_at,
        }
        async with self._sql.transaction() as db:
            await db.execute(
                """
                INSERT INTO mcp_client_credentials
                    (credential_id, client_id, binding_id, token_hash, token_prefix,
                     status, created_at, updated_at, expires_at)
                VALUES
                    (:credential_id, :client_id, :binding_id, :token_hash, :token_prefix,
                     :status, :created_at, :created_at, :expires_at)
                """,
                record,
            )
            await db.commit()
        return record, token

    async def rotate(
        self,
        credential_id: str,
        *,
        expires_in_seconds: int = DEFAULT_CREDENTIAL_TTL_SECONDS,
    ) -> tuple[dict[str, Any], str]:
        """Atomically replace one active credential within its existing binding."""
        if expires_in_seconds <= 0:
            raise ValueError("expires_in_seconds must be positive")
        replacement_id = f"cred_{uuid.uuid4().hex}"
        secret = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc).isoformat()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
        ).isoformat()
        async with self._sql.transaction() as db:
            async with db.execute(
                "SELECT client_id, binding_id, token_prefix FROM mcp_client_credentials "
                "WHERE credential_id = ? AND status = 'ACTIVE'",
                (credential_id,),
            ) as cursor:
                previous = await cursor.fetchone()
            if previous is None:
                raise ValueError("active credential not found")
            token_kind = _token_kind_from_prefix(str(previous[2]))
            token = f"mesa_{token_kind}_{replacement_id}_{secret}"
            record: dict[str, Any] = {
                "credential_id": replacement_id,
                "client_id": str(previous[0]),
                "binding_id": str(previous[1]),
                "token_hash": _token_hash(token),
                "token_prefix": token[:24],
                "status": "ACTIVE",
                "created_at": now,
                "expires_at": expires_at,
            }
            await db.execute(
                "INSERT INTO mcp_client_credentials "
                "(credential_id, client_id, binding_id, token_hash, token_prefix, status, created_at, updated_at, expires_at) "
                "VALUES (:credential_id, :client_id, :binding_id, :token_hash, :token_prefix, "
                ":status, :created_at, :created_at, :expires_at)",
                record,
            )
            cursor = await db.execute(
                "UPDATE mcp_client_credentials SET status = 'REVOKED', revoked_at = :now, updated_at = :now "
                "WHERE credential_id = :credential_id AND status = 'ACTIVE'",
                {"credential_id": credential_id, "now": now},
            )
            if cursor.rowcount != 1:
                raise RuntimeError("credential rotation fence lost")
            await db.commit()
        return record, token

    async def authenticate(self, token: str) -> dict[str, Any] | None:
        digest = _token_hash(token)
        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(
                """
                SELECT credential.*, client.enabled AS client_enabled, binding.enabled AS binding_enabled
                FROM mcp_client_credentials AS credential
                JOIN mcp_clients AS client ON client.client_id = credential.client_id
                JOIN mcp_project_bindings AS binding ON binding.binding_id = credential.binding_id
                WHERE credential.token_hash = :token_hash AND credential.status = 'ACTIVE'
                  AND (credential.expires_at IS NULL OR credential.expires_at > :now)
                """,
                {"token_hash": digest, "now": datetime.now(timezone.utc).isoformat()},
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        result = dict(row)
        if not result["client_enabled"] or not result["binding_enabled"]:
            return None
        now = datetime.now(timezone.utc).isoformat()
        async with self._sql.transaction() as db:
            await db.execute(
                "UPDATE mcp_client_credentials SET last_used_at = :now, updated_at = :now WHERE credential_id = :credential_id",
                {"now": now, "credential_id": result["credential_id"]},
            )
            await db.commit()
        return result

    async def revoke(self, credential_id: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        async with self._sql.transaction() as db:
            cursor = await db.execute(
                """UPDATE mcp_client_credentials
                   SET status = 'REVOKED', revoked_at = :now, updated_at = :now
                   WHERE credential_id = :credential_id AND status = 'ACTIVE'""",
                {"credential_id": credential_id, "now": now},
            )
            await db.commit()
        return bool(cursor.rowcount)

    async def list_for_binding(self, binding_id: str) -> list[dict[str, Any]]:
        """Return dashboard-safe credential summaries; token hashes stay private."""
        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(
                """SELECT credential_id, client_id, binding_id, token_prefix, status,
                          created_at, updated_at, last_used_at, revoked_at, expires_at
                   FROM mcp_client_credentials
                   WHERE binding_id = ? ORDER BY created_at DESC""",
                (binding_id,),
            ) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def get_summary(self, credential_id: str) -> dict[str, Any] | None:
        async with self._sql.connection() as db:
            db.row_factory = __import__("aiosqlite").Row
            async with db.execute(
                """SELECT credential_id, client_id, binding_id, token_prefix, status,
                          created_at, updated_at, last_used_at, revoked_at, expires_at
                   FROM mcp_client_credentials WHERE credential_id = ?""",
                (credential_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row else None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_kind_from_prefix(token_prefix: str) -> str:
    if token_prefix.startswith("mesa_antigravity_"):
        return "antigravity"
    if token_prefix.startswith("mesa_codex_"):
        return "codex"
    raise ValueError("stored credential has an unsupported token kind")
