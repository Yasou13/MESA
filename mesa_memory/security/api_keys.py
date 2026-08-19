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
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import aiosqlite

from mesa_storage.sqlite_engine import get_default_synchronous_mode

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 32
DEFAULT_API_KEY_TTL_SECONDS = 90 * 24 * 60 * 60


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

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        async with aiosqlite.connect(self.policy_path) as db:
            await db.execute(f"PRAGMA synchronous={get_default_synchronous_mode()};")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute("PRAGMA foreign_keys=ON;")
            yield db

    async def initialize(self) -> None:
        async with self._connect() as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS api_keys ("
                "key_id TEXT PRIMARY KEY, salt_b64 TEXT NOT NULL, digest_b64 TEXT NOT NULL, "
                "principal_id TEXT NOT NULL, principal_type TEXT NOT NULL, status TEXT NOT NULL, "
                "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, revoked_at TEXT, expires_at TEXT)"
            )
            async with db.execute("PRAGMA table_info(api_keys)") as cursor:
                columns = {str(row[1]) for row in await cursor.fetchall()}
            if "expires_at" not in columns:
                await db.execute("ALTER TABLE api_keys ADD COLUMN expires_at TEXT")
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
        expires_in_seconds: int = DEFAULT_API_KEY_TTL_SECONDS,
    ) -> str:
        """Create an active credential and return its plaintext exactly once."""
        if not principal_id:
            raise ValueError("principal_id is required")
        generated_id = key_id or f"mk_{secrets.token_urlsafe(12)}"
        if not generated_id.replace("_", "").replace("-", "").isalnum():
            raise ValueError("key_id contains unsupported characters")
        expires_at = _expiry_timestamp(expires_in_seconds)
        secret = secrets.token_urlsafe(32)
        await self._upsert_key(
            key_id=generated_id,
            secret=secret,
            principal_id=principal_id,
            principal_type=principal_type,
            status="active",
            replace=False,
            expires_at=expires_at,
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
        async with self._connect() as db:
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
                expires_at=None,
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
        expires_at: str | None,
    ) -> None:
        salt = os.urandom(16)
        digest = self._digest(secret, salt)
        statement = "INSERT OR REPLACE" if replace else "INSERT"
        async with self._connect() as db:
            await db.execute(
                f"{statement} INTO api_keys "
                "(key_id, salt_b64, digest_b64, principal_id, principal_type, status, revoked_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                (
                    key_id,
                    base64.b64encode(salt).decode("ascii"),
                    base64.b64encode(digest).decode("ascii"),
                    principal_id,
                    principal_type,
                    status,
                    expires_at,
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
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM api_keys WHERE key_id = ?", (key_id,)
            ) as cursor:
                row = await cursor.fetchone()
        if row is None or row["status"] != "active":
            return None
        expires_at = row["expires_at"]
        if expires_at is not None and str(expires_at) <= _now_timestamp():
            return None
        salt = base64.b64decode(row["salt_b64"])
        expected = base64.b64decode(row["digest_b64"])
        actual = self._digest(secret, salt)
        if not secrets.compare_digest(expected, actual):
            return None
        return VerifiedAPIKey(
            key_id=str(row["key_id"]),
            principal_id=str(row["principal_id"]),
            principal_type=str(row["principal_type"]),
            status=str(row["status"]),
        )

    async def has_active_key(self) -> bool:
        """Return whether an already-provisioned deployment key can boot."""
        async with self._connect() as db:
            async with db.execute(
                "SELECT 1 FROM api_keys WHERE status = 'active' "
                "AND (expires_at IS NULL OR expires_at > ?) LIMIT 1",
                (_now_timestamp(),),
            ) as cursor:
                return await cursor.fetchone() is not None

    async def revoke_key(self, key_id: str) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE api_keys SET status = 'revoked', revoked_at = CURRENT_TIMESTAMP "
                "WHERE key_id = ? AND status = 'active'",
                (key_id,),
            )
            await db.commit()
        return bool(cursor.rowcount == 1)

    async def rotate_key(self, key_id: str) -> str:
        """Atomically replace one active key without a no-key outage window."""
        replacement_id = f"mk_{secrets.token_urlsafe(12)}"
        replacement_secret = secrets.token_urlsafe(32)
        replacement_expiry = _expiry_timestamp(DEFAULT_API_KEY_TTL_SECONDS)
        salt = os.urandom(16)
        digest = self._digest(replacement_secret, salt)
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT principal_id, principal_type FROM api_keys WHERE key_id = ? AND status = 'active'",
                (key_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                await db.rollback()
                raise ValueError("active key not found")
            try:
                await db.execute(
                    "INSERT INTO api_keys "
                    "(key_id, salt_b64, digest_b64, principal_id, principal_type, status, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, 'active', ?)",
                    (
                        replacement_id,
                        base64.b64encode(salt).decode("ascii"),
                        base64.b64encode(digest).decode("ascii"),
                        row["principal_id"],
                        row["principal_type"],
                        replacement_expiry,
                    ),
                )
                cursor = await db.execute(
                    "UPDATE api_keys SET status = 'revoked', revoked_at = CURRENT_TIMESTAMP "
                    "WHERE key_id = ? AND status = 'active'",
                    (key_id,),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("key rotation fence lost")
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return f"{replacement_id}.{replacement_secret}"


def _now_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expiry_timestamp(expires_in_seconds: int) -> str:
    if expires_in_seconds <= 0:
        raise ValueError("expires_in_seconds must be positive")
    return (
        datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
    ).isoformat()
