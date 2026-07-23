"""Durable, rotation-safe API key registry.

Only a generated ``key_id.secret`` credential is ever returned by
``issue_key``.  SQLite retains the key identifier, principal binding and a
salted scrypt digest; it never stores a recoverable API secret.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass

import aiosqlite

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 32


@dataclass(frozen=True)
class VerifiedAPIKey:
    key_id: str
    principal_id: str
    principal_type: str
    status: str


class APIKeyStore:
    """Key-id addressed credential registry backed by the RBAC database."""

    def __init__(self, policy_path: str) -> None:
        self.policy_path = policy_path

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.policy_path) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS api_keys ("
                "key_id TEXT PRIMARY KEY, salt_b64 TEXT NOT NULL, digest_b64 TEXT NOT NULL, "
                "principal_id TEXT NOT NULL, principal_type TEXT NOT NULL, status TEXT NOT NULL, "
                "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, revoked_at TEXT)"
            )
            await db.commit()

    @staticmethod
    def _digest(secret: str, salt: bytes) -> bytes:
        return hashlib.scrypt(
            secret.encode("utf-8"),
            salt=salt,
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=_DKLEN,
        )

    async def issue_key(
        self,
        *,
        principal_id: str,
        principal_type: str = "SERVICE",
        key_id: str | None = None,
    ) -> str:
        """Create an active credential and return its plaintext exactly once."""
        if not principal_id:
            raise ValueError("principal_id is required")
        generated_id = key_id or f"mk_{secrets.token_urlsafe(12)}"
        if not generated_id.replace("_", "").replace("-", "").isalnum():
            raise ValueError("key_id contains unsupported characters")
        secret = secrets.token_urlsafe(32)
        await self._upsert_key(
            key_id=generated_id,
            secret=secret,
            principal_id=principal_id,
            principal_type=principal_type,
            status="active",
            replace=False,
        )
        return f"{generated_id}.{secret}"

    async def bootstrap_legacy_key(
        self, *, secret: str | None, principal_id: str | None, principal_type: str
    ) -> None:
        """Hash a legacy environment credential once for compatibility.

        The value is not persisted in plaintext.  New deployments should use
        key-id credentials issued by :meth:`issue_key` instead.
        """
        if not secret or not principal_id:
            return
        async with aiosqlite.connect(self.policy_path) as db:
            async with db.execute(
                "SELECT 1 FROM api_keys WHERE key_id = 'bootstrap'"
            ) as cursor:
                exists = await cursor.fetchone()
        if not exists:
            await self._upsert_key(
                key_id="bootstrap",
                secret=secret,
                principal_id=principal_id,
                principal_type=principal_type,
                status="active",
                replace=False,
            )

    async def _upsert_key(
        self,
        *,
        key_id: str,
        secret: str,
        principal_id: str,
        principal_type: str,
        status: str,
        replace: bool,
    ) -> None:
        salt = os.urandom(16)
        digest = self._digest(secret, salt)
        statement = "INSERT OR REPLACE" if replace else "INSERT"
        async with aiosqlite.connect(self.policy_path) as db:
            await db.execute(
                f"{statement} INTO api_keys "
                "(key_id, salt_b64, digest_b64, principal_id, principal_type, status, revoked_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (
                    key_id,
                    base64.b64encode(salt).decode("ascii"),
                    base64.b64encode(digest).decode("ascii"),
                    principal_id,
                    principal_type,
                    status,
                ),
            )
            await db.commit()

    async def verify(self, credential: str | None) -> VerifiedAPIKey | None:
        if not credential:
            return None
        key_id, separator, secret = credential.partition(".")
        if not separator:
            key_id, secret = "bootstrap", credential
        if not key_id or not secret:
            return None
        async with aiosqlite.connect(self.policy_path) as db:
            async with db.execute(
                "SELECT * FROM api_keys WHERE key_id = ?", (key_id,)
            ) as cursor:
                row = await cursor.fetchone()
        if row is None or row[5] != "active":
            return None
        salt = base64.b64decode(row[1])
        expected = base64.b64decode(row[2])
        actual = self._digest(secret, salt)
        if not secrets.compare_digest(expected, actual):
            return None
        return VerifiedAPIKey(
            key_id=str(row[0]),
            principal_id=str(row[3]),
            principal_type=str(row[4]),
            status=str(row[5]),
        )

    async def has_active_key(self) -> bool:
        """Return whether an already-provisioned deployment key can boot."""
        async with aiosqlite.connect(self.policy_path) as db:
            async with db.execute(
                "SELECT 1 FROM api_keys WHERE status = 'active' LIMIT 1"
            ) as cursor:
                return await cursor.fetchone() is not None

    async def revoke_key(self, key_id: str) -> bool:
        async with aiosqlite.connect(self.policy_path) as db:
            cursor = await db.execute(
                "UPDATE api_keys SET status = 'revoked', revoked_at = CURRENT_TIMESTAMP "
                "WHERE key_id = ? AND status = 'active'",
                (key_id,),
            )
            await db.commit()
        return bool(cursor.rowcount == 1)

    async def rotate_key(self, key_id: str) -> str:
        """Revoke one key and issue a replacement for the same principal."""
        async with aiosqlite.connect(self.policy_path) as db:
            async with db.execute(
                "SELECT principal_id, principal_type FROM api_keys WHERE key_id = ? AND status = 'active'",
                (key_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            raise ValueError("active key not found")
        if not await self.revoke_key(key_id):
            raise RuntimeError("key rotation fence lost")
        return await self.issue_key(
            principal_id=str(row[0]), principal_type=str(row[1])
        )
