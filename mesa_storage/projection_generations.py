"""Safe projection generation resolution and atomic runtime cutover."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import aiosqlite

from mesa_storage.sqlite_engine import AsyncEngine

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ProjectionGenerationError(RuntimeError):
    """Base class for content-free generation failures."""


class ProjectionGenerationNotFoundError(ProjectionGenerationError):
    """A generation or runtime pointer does not exist."""


class ProjectionGenerationConflictError(ProjectionGenerationError):
    """The requested generation lifecycle change is not valid."""


class ProjectionGenerationFencedError(ProjectionGenerationError):
    """The operation or runtime pointer fence is stale."""


class ProjectionPathError(ProjectionGenerationError):
    """A generation store path cannot be proven safe."""


@dataclass(frozen=True)
class ProjectionPaths:
    generation_id: str
    vector_path: Path
    graph_path: Path
    runtime_fencing_token: int
    previous_generation_id: str | None


class ProjectionGenerationRepositoryPort(Protocol):
    async def resolve_active(
        self, *, storage_root: Path, trusted_root: Path
    ) -> ProjectionPaths: ...

    async def create_staging(
        self,
        *,
        operation_id: str,
        generation_id: str,
        runner_id: str,
        claim_token: str,
        operation_fencing_token: int,
        source_manifest_hash: str | None = None,
        provider_manifest: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def activate(
        self,
        generation_id: str,
        *,
        operation_id: str,
        runner_id: str,
        claim_token: str,
        operation_fencing_token: int,
        expected_active_generation_id: str,
        runtime_fencing_token: int,
    ) -> dict[str, Any]: ...

    async def rollback(
        self,
        *,
        operation_id: str,
        runner_id: str,
        claim_token: str,
        operation_fencing_token: int,
        expected_active_generation_id: str,
        runtime_fencing_token: int,
    ) -> dict[str, Any]: ...


def _identifier(value: str, *, label: str) -> str:
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{label} is invalid")
    return value


def _operation_identifier(value: str) -> str:
    if not value or len(value) > 128 or any(ord(char) < 32 for char in value):
        raise ValueError("operation id is invalid")
    return value


def _manifest_hash(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.lower()
    if not _HASH_PATTERN.fullmatch(normalized):
        raise ValueError("source manifest hash must be a SHA-256 digest")
    return normalized


def _provider_manifest(value: dict[str, Any] | None) -> str:
    try:
        serialized = json.dumps(
            value or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("provider manifest must be JSON serializable") from exc
    if len(serialized.encode("utf-8")) > 16_384:
        raise ValueError("provider manifest exceeds the durable size limit")
    return serialized


def _safe_store_path(storage_root: Path, relative_value: str) -> Path:
    if "\\" in relative_value:
        raise ProjectionPathError("projection path is not a safe relative path")
    relative = Path(relative_value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ProjectionPathError("projection path is not a safe relative path")
    candidate = storage_root.joinpath(*relative.parts)
    current = candidate
    while current != storage_root:
        if current.is_symlink():
            raise ProjectionPathError("projection path contains a symlink")
        current = current.parent
    resolved = candidate.resolve(strict=False)
    if resolved == storage_root or not resolved.is_relative_to(storage_root):
        raise ProjectionPathError("projection path escapes the storage root")
    return resolved


def _resolve_paths(
    *,
    storage_root: Path,
    trusted_root: Path,
    generation_id: str,
    vector_relative_path: str,
    graph_relative_path: str,
    runtime_fencing_token: int,
    previous_generation_id: str | None,
) -> ProjectionPaths:
    try:
        trusted = trusted_root.resolve(strict=True)
        storage = storage_root.resolve(strict=True)
    except OSError as exc:
        raise ProjectionPathError("projection trust boundary does not exist") from exc
    if not trusted.is_dir() or not storage.is_dir():
        raise ProjectionPathError("projection trust boundary must be a directory")
    if storage != trusted and not storage.is_relative_to(trusted):
        raise ProjectionPathError("storage root escapes the trusted root")
    vector = _safe_store_path(storage, vector_relative_path)
    graph = _safe_store_path(storage, graph_relative_path)
    if vector == graph or vector.is_relative_to(graph) or graph.is_relative_to(vector):
        raise ProjectionPathError("projection store paths overlap")
    return ProjectionPaths(
        generation_id=generation_id,
        vector_path=vector,
        graph_path=graph,
        runtime_fencing_token=runtime_fencing_token,
        previous_generation_id=previous_generation_id,
    )


def resolve_projection_generation_paths(
    generation: dict[str, Any],
    *,
    storage_root: Path,
    trusted_root: Path,
    runtime_fencing_token: int,
    previous_generation_id: str | None = None,
) -> ProjectionPaths:
    """Resolve one persisted generation without allowing path escape or symlinks."""
    return _resolve_paths(
        storage_root=storage_root,
        trusted_root=trusted_root,
        generation_id=str(generation["generation_id"]),
        vector_relative_path=str(generation["vector_relative_path"]),
        graph_relative_path=str(generation["graph_relative_path"]),
        runtime_fencing_token=runtime_fencing_token,
        previous_generation_id=previous_generation_id,
    )


class ProjectionGenerationRepository:
    """Own generation metadata and the single atomic runtime pointer."""

    __slots__ = ("_sql",)

    def __init__(self, sqlite_engine: AsyncEngine) -> None:
        self._sql = sqlite_engine

    async def _operation(
        self, db: aiosqlite.Connection, operation_id: str
    ) -> aiosqlite.Row | None:
        cursor = await db.execute(
            "SELECT *, CASE WHEN lease_expires_at > CURRENT_TIMESTAMP "
            "THEN 1 ELSE 0 END AS lease_valid "
            "FROM system_operations WHERE operation_id = ?",
            (operation_id,),
        )
        return await cursor.fetchone()

    @staticmethod
    def _assert_operation_fence(
        operation: aiosqlite.Row,
        *,
        runner_id: str,
        claim_token: str,
        operation_fencing_token: int,
        allowed_states: set[str],
    ) -> None:
        if (
            operation["state"] not in allowed_states
            or operation["claimed_by"] != runner_id
            or operation["claim_token"] != claim_token
            or int(operation["fencing_token"]) != operation_fencing_token
            or not bool(operation["lease_valid"])
        ):
            raise ProjectionGenerationFencedError(
                "projection operation lease or fence is stale"
            )

    async def resolve_active(
        self, *, storage_root: Path, trusted_root: Path
    ) -> ProjectionPaths:
        async with self._sql.connection() as db:
            cursor = await db.execute(
                "SELECT r.active_generation_id, r.previous_generation_id, "
                "r.fencing_token, g.vector_relative_path, g.graph_relative_path, "
                "g.lifecycle_state FROM projection_runtime r "
                "JOIN projection_generations g "
                "ON g.generation_id = r.active_generation_id "
                "WHERE r.runtime_id = 1"
            )
            row = await cursor.fetchone()
        if row is None or row["lifecycle_state"] != "ACTIVE":
            raise ProjectionGenerationNotFoundError(
                "active projection generation is unavailable"
            )
        return _resolve_paths(
            storage_root=storage_root,
            trusted_root=trusted_root,
            generation_id=str(row["active_generation_id"]),
            vector_relative_path=str(row["vector_relative_path"]),
            graph_relative_path=str(row["graph_relative_path"]),
            runtime_fencing_token=int(row["fencing_token"]),
            previous_generation_id=(
                str(row["previous_generation_id"])
                if row["previous_generation_id"] is not None
                else None
            ),
        )

    async def create_staging(
        self,
        *,
        operation_id: str,
        generation_id: str,
        runner_id: str,
        claim_token: str,
        operation_fencing_token: int,
        source_manifest_hash: str | None = None,
        provider_manifest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operation_identifier = _operation_identifier(operation_id)
        generation = _identifier(generation_id, label="generation id")
        if generation == "legacy":
            raise ValueError("legacy generation id is reserved")
        runner = _identifier(runner_id, label="runner id")
        token = _operation_identifier(claim_token)
        manifest_hash = _manifest_hash(source_manifest_hash)
        manifest_json = _provider_manifest(provider_manifest)
        vector_relative = f"projection-generations/{generation}/vector.lance"
        graph_relative = f"projection-generations/{generation}/kuzu_db"

        async with self._sql.transaction() as db:
            operation = await self._operation(db, operation_identifier)
            if operation is None:
                raise ProjectionGenerationNotFoundError(
                    "projection operation is unavailable"
                )
            self._assert_operation_fence(
                operation,
                runner_id=runner,
                claim_token=token,
                operation_fencing_token=operation_fencing_token,
                allowed_states={"RUNNING"},
            )
            cursor = await db.execute(
                "SELECT * FROM projection_generations WHERE generation_id = ?",
                (generation,),
            )
            existing = await cursor.fetchone()
            if existing is not None:
                if (
                    existing["created_by_operation_id"] != operation_identifier
                    or existing["vector_relative_path"] != vector_relative
                    or existing["graph_relative_path"] != graph_relative
                    or existing["source_manifest_hash"] != manifest_hash
                    or existing["provider_manifest_json"] != manifest_json
                ):
                    raise ProjectionGenerationConflictError(
                        "projection generation identity conflicts"
                    )
                await db.commit()
                return dict(existing)
            try:
                await db.execute(
                    "INSERT INTO projection_generations ("
                    "generation_id, generation_kind, lifecycle_state, "
                    "vector_relative_path, graph_relative_path, "
                    "source_manifest_hash, provider_manifest_json, "
                    "created_by_operation_id"
                    ") VALUES (?, 'REBUILD', 'STAGING', ?, ?, ?, ?, ?)",
                    (
                        generation,
                        vector_relative,
                        graph_relative,
                        manifest_hash,
                        manifest_json,
                        operation_identifier,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ProjectionGenerationConflictError(
                    "projection generation paths conflict"
                ) from exc
            await db.commit()
            cursor = await db.execute(
                "SELECT * FROM projection_generations WHERE generation_id = ?",
                (generation,),
            )
            created = await cursor.fetchone()
            assert created is not None
            return dict(created)

    async def activate(
        self,
        generation_id: str,
        *,
        operation_id: str,
        runner_id: str,
        claim_token: str,
        operation_fencing_token: int,
        expected_active_generation_id: str,
        runtime_fencing_token: int,
    ) -> dict[str, Any]:
        generation = _identifier(generation_id, label="generation id")
        operation_identifier = _operation_identifier(operation_id)
        runner = _identifier(runner_id, label="runner id")
        token = _operation_identifier(claim_token)
        expected_active = _identifier(
            expected_active_generation_id, label="active generation id"
        )
        async with self._sql.transaction() as db:
            operation = await self._operation(db, operation_identifier)
            if operation is None:
                raise ProjectionGenerationNotFoundError(
                    "projection operation is unavailable"
                )
            self._assert_operation_fence(
                operation,
                runner_id=runner,
                claim_token=token,
                operation_fencing_token=operation_fencing_token,
                allowed_states={"READY_TO_CUTOVER"},
            )
            if operation["target_generation_id"] != generation:
                raise ProjectionGenerationConflictError(
                    "operation target generation does not match"
                )
            cursor = await db.execute(
                "SELECT * FROM projection_runtime WHERE runtime_id = 1"
            )
            runtime = await cursor.fetchone()
            if runtime is None:
                raise ProjectionGenerationNotFoundError(
                    "projection runtime pointer is unavailable"
                )
            if (
                runtime["active_generation_id"] != expected_active
                or int(runtime["fencing_token"]) != runtime_fencing_token
            ):
                raise ProjectionGenerationFencedError(
                    "projection runtime pointer fence is stale"
                )
            cursor = await db.execute(
                "SELECT generation_id, lifecycle_state FROM projection_generations "
                "WHERE generation_id IN (?, ?)",
                (expected_active, generation),
            )
            states = {
                str(row["generation_id"]): str(row["lifecycle_state"])
                for row in await cursor.fetchall()
            }
            if (
                states.get(expected_active) != "ACTIVE"
                or states.get(generation) != "STAGING"
            ):
                raise ProjectionGenerationConflictError(
                    "projection generations are not cutover-ready"
                )
            await db.execute(
                "UPDATE projection_generations SET lifecycle_state = 'RETAINED', "
                "retained_at = CURRENT_TIMESTAMP WHERE generation_id = ? "
                "AND lifecycle_state = 'ACTIVE'",
                (expected_active,),
            )
            await db.execute(
                "UPDATE projection_generations SET lifecycle_state = 'ACTIVE', "
                "activated_at = CURRENT_TIMESTAMP WHERE generation_id = ? "
                "AND lifecycle_state = 'STAGING'",
                (generation,),
            )
            cursor = await db.execute(
                "UPDATE projection_runtime SET active_generation_id = ?, "
                "previous_generation_id = ?, fencing_token = fencing_token + 1, "
                "updated_at = CURRENT_TIMESTAMP WHERE runtime_id = 1 "
                "AND active_generation_id = ? AND fencing_token = ? AND EXISTS ("
                "SELECT 1 FROM system_operations WHERE operation_id = ? "
                "AND state = 'READY_TO_CUTOVER' AND claimed_by = ? "
                "AND claim_token = ? AND fencing_token = ? "
                "AND lease_expires_at > CURRENT_TIMESTAMP)",
                (
                    generation,
                    expected_active,
                    expected_active,
                    runtime_fencing_token,
                    operation_identifier,
                    runner,
                    token,
                    operation_fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise ProjectionGenerationFencedError(
                    "projection activation was fenced out"
                )
            await db.commit()
            cursor = await db.execute(
                "SELECT * FROM projection_runtime WHERE runtime_id = 1"
            )
            activated = await cursor.fetchone()
            assert activated is not None
            return dict(activated)

    async def rollback(
        self,
        *,
        operation_id: str,
        runner_id: str,
        claim_token: str,
        operation_fencing_token: int,
        expected_active_generation_id: str,
        runtime_fencing_token: int,
    ) -> dict[str, Any]:
        operation_identifier = _operation_identifier(operation_id)
        runner = _identifier(runner_id, label="runner id")
        token = _operation_identifier(claim_token)
        expected_active = _identifier(
            expected_active_generation_id, label="active generation id"
        )
        async with self._sql.transaction() as db:
            operation = await self._operation(db, operation_identifier)
            if operation is None:
                raise ProjectionGenerationNotFoundError(
                    "projection operation is unavailable"
                )
            self._assert_operation_fence(
                operation,
                runner_id=runner,
                claim_token=token,
                operation_fencing_token=operation_fencing_token,
                allowed_states={"READY_TO_CUTOVER"},
            )
            if operation["target_generation_id"] != expected_active:
                raise ProjectionGenerationConflictError(
                    "operation target generation does not match active generation"
                )
            cursor = await db.execute(
                "SELECT * FROM projection_runtime WHERE runtime_id = 1"
            )
            runtime = await cursor.fetchone()
            if runtime is None or runtime["previous_generation_id"] is None:
                raise ProjectionGenerationConflictError(
                    "projection runtime has no retained rollback generation"
                )
            retained = str(runtime["previous_generation_id"])
            if (
                runtime["active_generation_id"] != expected_active
                or int(runtime["fencing_token"]) != runtime_fencing_token
            ):
                raise ProjectionGenerationFencedError(
                    "projection runtime pointer fence is stale"
                )
            cursor = await db.execute(
                "SELECT generation_id, lifecycle_state FROM projection_generations "
                "WHERE generation_id IN (?, ?)",
                (expected_active, retained),
            )
            states = {
                str(row["generation_id"]): str(row["lifecycle_state"])
                for row in await cursor.fetchall()
            }
            if (
                states.get(expected_active) != "ACTIVE"
                or states.get(retained) != "RETAINED"
            ):
                raise ProjectionGenerationConflictError(
                    "projection generations are not rollback-ready"
                )
            await db.execute(
                "UPDATE projection_generations SET lifecycle_state = 'FAILED' "
                "WHERE generation_id = ? AND lifecycle_state = 'ACTIVE'",
                (expected_active,),
            )
            await db.execute(
                "UPDATE projection_generations SET lifecycle_state = 'ACTIVE', "
                "activated_at = CURRENT_TIMESTAMP WHERE generation_id = ? "
                "AND lifecycle_state = 'RETAINED'",
                (retained,),
            )
            cursor = await db.execute(
                "UPDATE projection_runtime SET active_generation_id = ?, "
                "previous_generation_id = NULL, fencing_token = fencing_token + 1, "
                "updated_at = CURRENT_TIMESTAMP WHERE runtime_id = 1 "
                "AND active_generation_id = ? AND previous_generation_id = ? "
                "AND fencing_token = ? AND EXISTS ("
                "SELECT 1 FROM system_operations WHERE operation_id = ? "
                "AND state = 'READY_TO_CUTOVER' AND claimed_by = ? "
                "AND claim_token = ? AND fencing_token = ? "
                "AND lease_expires_at > CURRENT_TIMESTAMP)",
                (
                    retained,
                    expected_active,
                    retained,
                    runtime_fencing_token,
                    operation_identifier,
                    runner,
                    token,
                    operation_fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise ProjectionGenerationFencedError(
                    "projection rollback was fenced out"
                )
            await db.commit()
            cursor = await db.execute(
                "SELECT * FROM projection_runtime WHERE runtime_id = 1"
            )
            rolled_back = await cursor.fetchone()
            assert rolled_back is not None
            return dict(rolled_back)
