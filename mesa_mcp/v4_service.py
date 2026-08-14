"""MCP-facing adapter for the MESA V4 HTTP application service."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from typing import Any

from mesa_client.client import AsyncMesaV4Client, MesaAPIError, MesaNetworkError

from .bounded_cache import BoundedLRUCache
from .configuration import MCPSettings
from .errors import MCPError


class MesaHttpV4Service:
    """Use MESA's V4 public API; this class never imports a storage backend."""

    def __init__(self, settings: MCPSettings):
        self._settings = settings
        self._session_cache: BoundedLRUCache[str, str] = BoundedLRUCache(max_size=512)
        self._session_locks: BoundedLRUCache[str, asyncio.Lock] = BoundedLRUCache(max_size=512)
        self._http_client = AsyncMesaV4Client(
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout=8.0,
            max_retries=0,
        )

    async def close(self) -> None:
        await self._http_client.aclose()

    async def health(self) -> dict[str, Any]:
        try:
            return await self._http_client._request("GET", "/health")
        except Exception as exc:
            raise _map_exception(exc) from exc

    async def v4_capability(self) -> dict[str, Any]:
        """Return API-authored capability truth without adding MCP controls."""
        try:
            return await self._http_client.capability()
        except Exception as exc:
            raise _map_exception(exc) from exc

    async def _get_session_id(
        self,
        client: AsyncMesaV4Client,
        dataset_id: str,
        *,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        actor_id: str | None = None,
    ) -> str:
        tenant_id = tenant_id or self._settings.default_tenant_id
        workspace_id = workspace_id or self._settings.default_workspace_id
        actor_id = actor_id or self._settings.actor_id
        cache_key = self._session_cache_key(
            tenant_id, workspace_id, actor_id, dataset_id
        )
        lock = self._session_locks.setdefault(cache_key, asyncio.Lock)
        async with lock:
            cached = self._session_cache.get(cache_key)
            if cached is not None:
                return cached
            try:
                resp = await client.start_session(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    dataset_ids=[dataset_id],
                    agent_id=actor_id,
                )
                session_id = resp["session_id"]
                self._session_cache.put(cache_key, session_id)
                return session_id
            except Exception as exc:
                raise _map_exception(exc) from exc

    @staticmethod
    def _session_cache_key(
        tenant_id: str, workspace_id: str, actor_id: str, dataset_id: str
    ) -> str:
        return f"{tenant_id}:{workspace_id}:{actor_id}:{dataset_id}"

    def _invalidate_session(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        dataset_id: str,
        session_id: str,
    ) -> None:
        cache_key = self._session_cache_key(
            tenant_id, workspace_id, actor_id, dataset_id
        )
        if self._session_cache.get(cache_key) == session_id:
            self._session_cache.pop(cache_key, None)

    @staticmethod
    def _physical_identity_seed(
        *,
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
        actor_id: str,
        operation_type: str,
        idempotency_key: str,
    ) -> str:
        scope = "\x1f".join(
            (
                tenant_id,
                workspace_id,
                dataset_id,
                actor_id,
                operation_type,
                idempotency_key,
            )
        )
        return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _is_inactive_session_conflict(exc: MesaAPIError) -> bool:
        return exc.status_code == 409 and exc.error == "SESSION_INACTIVE"

    async def _verify_existing_document(
        self,
        client: AsyncMesaV4Client,
        *,
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
        document_id: str,
        title: str,
    ) -> bool:
        response = await client.list_documents(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            dataset_id=dataset_id,
        )
        return any(
            isinstance(item, dict)
            and item.get("document_id") == document_id
            and item.get("tenant_id") == tenant_id
            and item.get("dataset_id") == dataset_id
            and item.get("title") == title
            for item in response.get("documents", [])
        )

    async def v4_remember(self, **kwargs: Any) -> dict[str, Any]:
        dataset_id = kwargs.get("dataset_id") or self._settings.default_dataset_id
        tenant_id = kwargs.get("tenant_id") or self._settings.default_tenant_id
        workspace_id = kwargs.get("workspace_id") or self._settings.default_workspace_id
        actor_id = kwargs.get("actor_id") or self._settings.actor_id
        idempotency_key = kwargs.get("idempotency_key")
        if isinstance(idempotency_key, str) and idempotency_key:
            # V4 provenance IDs are global, while an MCP retry must retain the
            # same physical identities.  Derive all three from the durable
            # idempotency key instead of reusing `rev_1` / `chunk_1` across
            # unrelated documents.
            seed = self._physical_identity_seed(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                actor_id=actor_id,
                operation_type="REMEMBER",
                idempotency_key=idempotency_key,
            )
            document_id = kwargs.get("document_id") or f"doc_{seed}"
            revision_id = f"rev_{seed}"
            chunk_id = f"chunk_{seed}"
        else:
            seed = uuid.uuid4().hex
            document_id = kwargs.get("document_id") or f"doc_{seed[:24]}"
            revision_id = f"rev_{seed}"
            chunk_id = f"chunk_{seed}"
        content = kwargs["content"]

        client = self._http_client
        session_id = await self._get_session_id(
            client,
            dataset_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
        )

        title = kwargs.get("title", f"Memory {document_id}")
        try:
            await client.create_document(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                document_id=document_id,
                title=title,
            )
        except MesaAPIError as exc:
            if exc.status_code != 409 or not await self._verify_existing_document(
                client,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                document_id=document_id,
                title=title,
            ):
                raise _map_exception(exc) from exc
        insert_arguments = {
            "dataset_id": dataset_id,
            "document_id": document_id,
            "revision_id": revision_id,
            "chunk_id": chunk_id,
            "title": title,
            "source_ref": kwargs.get("source_ref", "mcp_tool"),
            "evidence_span": kwargs.get("evidence_span", ""),
            "content": content,
            "metadata": kwargs.get("metadata", {}),
            "idempotency_key": kwargs.get("idempotency_key"),
        }
        try:
            return await client.insert(session_id=session_id, **insert_arguments)
        except MesaAPIError as exc:
            if not self._is_inactive_session_conflict(exc):
                raise _map_exception(exc) from exc
            self._invalidate_session(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                dataset_id=dataset_id,
                session_id=session_id,
            )
            session_id = await self._get_session_id(
                client,
                dataset_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
            )
            try:
                return await client.insert(session_id=session_id, **insert_arguments)
            except Exception as retry_exc:
                raise _map_exception(retry_exc) from retry_exc
        except Exception as exc:
            raise _map_exception(exc) from exc

    async def v4_mutation_status(self, mutation_id: str) -> dict[str, Any]:
        """Return the durable V4 mutation state for an admitted write."""
        try:
            return await self._http_client.status(mutation_id)
        except Exception as exc:
            raise _map_exception(exc) from exc

    async def v4_recall(self, **kwargs: Any) -> list[dict[str, Any]]:
        dataset_id = kwargs.get("dataset_id") or self._settings.default_dataset_id
        tenant_id = kwargs.get("tenant_id") or self._settings.default_tenant_id
        workspace_id = kwargs.get("workspace_id") or self._settings.default_workspace_id
        actor_id = kwargs.get("actor_id") or self._settings.actor_id

        client = self._http_client
        session_id = await self._get_session_id(
            client,
            dataset_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
        )
        search_arguments = {
            "query": kwargs["query"],
            "dataset_ids": [dataset_id],
            "limit": kwargs.get("limit") or self._settings.search_default_limit,
        }
        search_arguments.update(
            {
                key: kwargs[key]
                for key in ("valid_at", "valid_from", "valid_to")
                if kwargs.get(key) is not None
            }
        )
        try:
            resp = await client.search(session_id=session_id, **search_arguments)
        except MesaAPIError as exc:
            if not self._is_inactive_session_conflict(exc):
                raise _map_exception(exc) from exc
            self._invalidate_session(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                dataset_id=dataset_id,
                session_id=session_id,
            )
            session_id = await self._get_session_id(
                client,
                dataset_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
            )
            try:
                resp = await client.search(session_id=session_id, **search_arguments)
            except Exception as retry_exc:
                raise _map_exception(retry_exc) from retry_exc
        except Exception as exc:
            raise _map_exception(exc) from exc
        return [_typed_result(item) for item in resp.get("results", [])]

    async def v4_context(self, **kwargs: Any) -> dict[str, Any]:
        """Build context through the canonical V4 ContextBuilder endpoint."""
        dataset_id = kwargs.get("dataset_id") or self._settings.default_dataset_id
        tenant_id = kwargs.get("tenant_id") or self._settings.default_tenant_id
        workspace_id = kwargs.get("workspace_id") or self._settings.default_workspace_id
        actor_id = kwargs.get("actor_id") or self._settings.actor_id
        client = self._http_client
        session_id = await self._get_session_id(
            client,
            dataset_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
        )
        context_arguments = {
            "query": kwargs.get("query", ""),
            "token_budget": kwargs.get(
                "token_budget", self._settings.context_default_token_budget
            ),
        }
        context_arguments.update(
            {
                key: kwargs[key]
                for key in ("valid_at", "valid_from", "valid_to")
                if kwargs.get(key) is not None
            }
        )
        try:
            return await client.get_context(session_id=session_id, **context_arguments)
        except MesaAPIError as exc:
            if not self._is_inactive_session_conflict(exc):
                raise _map_exception(exc) from exc
            self._invalidate_session(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                dataset_id=dataset_id,
                session_id=session_id,
            )
            session_id = await self._get_session_id(
                client,
                dataset_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
            )
            try:
                return await client.get_context(
                    session_id=session_id, **context_arguments
                )
            except Exception as retry_exc:
                raise _map_exception(retry_exc) from retry_exc
        except Exception as exc:
            raise _map_exception(exc) from exc

    async def v4_improve(self, **kwargs: Any) -> dict[str, Any]:
        """Admit a corrected revision through the same canonical write path."""
        dataset_id = kwargs.get("dataset_id") or self._settings.default_dataset_id
        tenant_id = kwargs.get("tenant_id") or self._settings.default_tenant_id
        workspace_id = kwargs.get("workspace_id") or self._settings.default_workspace_id
        actor_id = kwargs.get("actor_id") or self._settings.actor_id
        document_id = kwargs["document_id"]
        content = kwargs["content"]
        idempotency_key = kwargs.get("idempotency_key")
        identity_seed = (
            self._physical_identity_seed(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                actor_id=actor_id,
                operation_type="IMPROVE",
                idempotency_key=str(idempotency_key),
            )
            if idempotency_key
            else uuid.uuid4().hex
        )
        revision_id = kwargs.get("revision_id") or f"rev_{identity_seed}"
        chunk_id = kwargs.get("chunk_id") or f"chunk_{identity_seed}"

        client = self._http_client
        supersedes_revision_id = kwargs.get("supersedes_revision_id")
        revision_number = kwargs.get("revision_number")
        latest: dict[str, Any] | None = None
        revisions: list[dict[str, Any]] = []
        if supersedes_revision_id is None:
            try:
                revision_response = await client.list_revisions(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    dataset_id=dataset_id,
                    document_id=document_id,
                )
            except Exception as exc:
                raise _map_exception(exc) from exc
            revisions = [
                item
                for item in revision_response.get("revisions", [])
                if isinstance(item, dict)
            ]
            latest = max(
                (item for item in revisions if item.get("status") == "ACTIVE"),
                key=lambda item: int(item.get("revision_number", 0)),
                default=None,
            )
            if latest is not None:
                supersedes_revision_id = latest.get("revision_id")
        if revision_number is None:
            revision_number = (
                max(
                    (int(item.get("revision_number", 0)) for item in revisions),
                    default=0,
                )
                + 1
            )
            if not revisions and supersedes_revision_id:
                revision_number = 2
        revision_number = int(revision_number)
        session_id = await self._get_session_id(
            client,
            dataset_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
        )
        try:
            return await client.insert(
                session_id=session_id,
                dataset_id=dataset_id,
                document_id=document_id,
                revision_id=revision_id,
                chunk_id=chunk_id,
                title=kwargs.get("title", f"Correction {document_id}"),
                source_ref=kwargs.get("source_ref", "mcp_correction"),
                content=content,
                evidence_span=kwargs.get("evidence_span", ""),
                revision_number=revision_number,
                metadata=kwargs.get("metadata", {}),
                idempotency_key=idempotency_key,
                supersedes_revision_id=supersedes_revision_id,
            )
        except Exception as exc:
            raise _map_exception(exc) from exc

    async def v4_forget(self, **kwargs: Any) -> dict[str, Any]:
        dataset_id = kwargs.get("dataset_id") or self._settings.default_dataset_id
        tenant_id = kwargs.get("tenant_id") or self._settings.default_tenant_id
        workspace_id = kwargs.get("workspace_id") or self._settings.default_workspace_id
        document_id = kwargs["document_id"]

        client = self._http_client
        try:
            return await client.purge_document(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                document_id=document_id,
            )
        except Exception as exc:
            raise _map_exception(exc) from exc


def _typed_result(item: dict[str, Any]) -> dict[str, Any]:
    entity = item.get("entity")
    entity = entity if isinstance(entity, dict) else {}
    raw_provenance = item.get("provenance")
    assertions = raw_provenance if isinstance(raw_provenance, list) else []
    primary_assertion = next(
        (assertion for assertion in assertions if isinstance(assertion, dict)), {}
    )
    metadata = item.get("metadata") or primary_assertion.get("metadata") or {}
    metadata = metadata if isinstance(metadata, dict) else {}
    provenance: dict[str, Any]
    if assertions:
        provenance = {
            "entity_id": entity.get("entity_id"),
            "assertions": assertions,
        }
    elif isinstance(raw_provenance, dict):
        provenance = raw_provenance
    else:
        provenance = {}
    return {
        "memory_id": item.get("memory_id")
        or entity.get("entity_id")
        or item.get("chunk_id"),
        "document_id": item.get("document_id") or primary_assertion.get("document_id"),
        "chunk_id": item.get("chunk_id") or primary_assertion.get("chunk_id"),
        "content": item.get("content") or entity.get("canonical_name"),
        "memory_type": metadata.get("memory_type", "unknown"),
        "status": item.get("status") or entity.get("status", "active"),
        "score": item.get("score")
        or item.get("final_score")
        or item.get("rrf_score", 0.0),
        "provenance": provenance,
    }


def _map_exception(exc: Exception) -> MCPError:
    if isinstance(exc, MCPError):
        return exc
    if isinstance(exc, MesaNetworkError):
        return MCPError(
            "BACKEND_UNAVAILABLE", "MESA service is unavailable", retryable=True
        )
    if isinstance(exc, MesaAPIError):
        if exc.status_code in {400, 422}:
            return MCPError("INVALID_ARGUMENT", "MESA rejected the request")
        if exc.status_code in {401, 403}:
            return MCPError(
                "ACCESS_DENIED", "MESA denied access to the requested scope"
            )
        if exc.status_code == 404:
            return MCPError("NOT_FOUND", "memory was not found")
        if exc.status_code == 409:
            return MCPError(exc.error or "CONFLICT", exc.detail)
        if exc.status_code in {408, 429, 503, 504}:
            return MCPError(
                "BACKEND_UNAVAILABLE",
                "MESA service is temporarily unavailable",
                retryable=True,
            )
    return MCPError("INTERNAL_ERROR", "MESA operation failed")
