"""
MESA v0.3.0 Client SDK

A robust, type-safe Python client for the MESA memory system.
Provides both synchronous and asynchronous implementations utilizing httpx.
Strictly relies on Pydantic V2 schemas from the core API to ensure type safety.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Optional, TypeVar

import httpx
from pydantic import ValidationError

from mesa_api.schemas import (
    ErrorResponse,
    MemoryInsertRequest,
    MemoryInsertResponse,
    MemoryPurgeRequest,
    MemoryPurgeResponse,
    MemorySearchRequest,
    MemorySearchResponse,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class MesaClientError(Exception):
    """Base exception for MESA client errors."""

    pass


class MesaAPIError(MesaClientError):
    """Exception raised when the MESA API returns an error response."""

    def __init__(self, status_code: int, error: str, detail: str):
        super().__init__(f"{status_code} {error}: {detail}")
        self.status_code = status_code
        self.error = error
        self.detail = detail


class MesaNetworkError(MesaClientError):
    """Exception raised for network-related errors (timeouts, connection issues)."""

    pass


class MesaValidationError(MesaClientError):
    """Exception raised when Pydantic validation fails for request or response payloads."""

    pass


def _parse_api_error(response: httpx.Response) -> MesaAPIError:
    """Parses an API error response and converts it into a MesaAPIError."""
    try:
        data = response.json()
        error_resp = ErrorResponse(**data)
        return MesaAPIError(error_resp.status_code, error_resp.error, error_resp.detail)
    except Exception:
        # Fallback if the error format does not match ErrorResponse schema
        return MesaAPIError(response.status_code, "UnknownError", response.text)


def _sync_retry(
    operation: Callable[[], httpx.Response],
    max_retries: int,
    base_delay: float = 0.5,
) -> httpx.Response:
    """Retry logic for synchronous HTTP operations."""
    attempt = 0
    while True:
        try:
            return operation()
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            attempt += 1

            # Unsafe retry prevention (R-18)
            method = (
                getattr(e.request, "method", "").upper()
                if hasattr(e, "request")
                else ""
            )
            is_idempotent = method in ("GET", "HEAD", "OPTIONS", "PUT", "DELETE")
            is_safe_error = isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout))

            if method and not is_idempotent and not is_safe_error:
                raise MesaNetworkError(
                    f"Refusing to retry non-idempotent {method} request: {e}"
                ) from e

            if attempt >= max_retries:
                raise MesaNetworkError(
                    f"Max retries ({max_retries}) exceeded. Last error: {e}"
                ) from e
            time.sleep(base_delay * (2 ** (attempt - 1)))


async def _async_retry(
    operation: Callable[[], Any],
    max_retries: int,
    base_delay: float = 0.5,
) -> httpx.Response:
    """Retry logic for asynchronous HTTP operations."""
    attempt = 0
    while True:
        try:
            return await operation()  # type: ignore[no-any-return]
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            attempt += 1

            # Unsafe retry prevention (R-18)
            method = (
                getattr(e.request, "method", "").upper()
                if hasattr(e, "request")
                else ""
            )
            is_idempotent = method in ("GET", "HEAD", "OPTIONS", "PUT", "DELETE")
            is_safe_error = isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout))

            if method and not is_idempotent and not is_safe_error:
                raise MesaNetworkError(
                    f"Refusing to retry non-idempotent {method} request: {e}"
                ) from e

            if attempt >= max_retries:
                raise MesaNetworkError(
                    f"Max retries ({max_retries}) exceeded. Last error: {e}"
                ) from e
            await asyncio.sleep(base_delay * (2 ** (attempt - 1)))


class MesaClient:
    """Synchronous MESA API client.

    Provides blocking methods to interact with the MESA memory system endpoints.
    Includes built-in retries for network resilience and enforces Pydantic V2 schemas.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        timeout: float = 10.0,
        max_retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        headers = {"User-Agent": "mesa-client/v0.6.1"}
        if api_key:
            headers["X-API-Key"] = api_key

        self._client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout,
        )

    def close(self) -> None:
        """Closes the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> "MesaClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Internal request executor with error handling and retry logic."""

        def _op() -> httpx.Response:
            response = self._client.request(method, path, **kwargs)
            # Catch 4XX and 5XX status codes
            if 400 <= response.status_code < 600:
                raise _parse_api_error(response)

            # Version compatibility check
            server_version = response.headers.get("X-API-Version")
            if server_version and not server_version.startswith("0."):
                logger.warning(
                    f"SDK/API Version mismatch: SDK expects 0.x.x, Server is {server_version}. "
                    f"Compatibility is not guaranteed."
                )

            return response

        resp = _sync_retry(_op, self.max_retries)
        return resp.json()  # type: ignore[str, Any]

    def insert(self, request: MemoryInsertRequest) -> MemoryInsertResponse:
        """POST /v3/memory/insert - Insert memory into the MESA system.

        Args:
            request: MemoryInsertRequest instance.
        Returns:
            MemoryInsertResponse instance.
        """
        try:
            payload = request.model_dump(mode="json")
            data = self._request("POST", "/v3/memory/insert", json=payload)
            return MemoryInsertResponse(**data)
        except ValidationError as e:
            raise MesaValidationError(f"Response validation failed: {e}") from e

    def search(self, request: MemorySearchRequest) -> MemorySearchResponse:
        """POST /v3/memory/search - Search memory in the MESA system.

        Args:
            request: MemorySearchRequest instance.
        Returns:
            MemorySearchResponse instance.
        """
        try:
            payload = request.model_dump(mode="json")
            data = self._request("POST", "/v3/memory/search", json=payload)
            return MemorySearchResponse(**data)
        except ValidationError as e:
            raise MesaValidationError(f"Response validation failed: {e}") from e

    def purge(self, request: MemoryPurgeRequest) -> MemoryPurgeResponse:
        """DELETE /v3/memory/purge - Purge (soft-delete) memory in the MESA system.

        Args:
            request: MemoryPurgeRequest instance.
        Returns:
            MemoryPurgeResponse instance.
        """
        try:
            payload = request.model_dump(mode="json")
            data = self._request("DELETE", "/v3/memory/purge", json=payload)
            return MemoryPurgeResponse(**data)
        except ValidationError as e:
            raise MesaValidationError(f"Response validation failed: {e}") from e


class AsyncMesaClient:
    """Asynchronous MESA API client.

    Provides non-blocking methods to interact with the MESA memory system endpoints.
    Includes built-in retries for network resilience and enforces Pydantic V2 schemas.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        timeout: float = 10.0,
        max_retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        headers = {"User-Agent": "mesa-client/v0.6.1"}
        if api_key:
            headers["X-API-Key"] = api_key

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout,
        )

    async def aclose(self) -> None:
        """Closes the underlying HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncMesaClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Internal request executor with error handling and retry logic."""

        async def _op() -> httpx.Response:
            response = await self._client.request(method, path, **kwargs)
            # Catch 4XX and 5XX status codes
            if 400 <= response.status_code < 600:
                raise _parse_api_error(response)

            # Version compatibility check
            server_version = response.headers.get("X-API-Version")
            if server_version and not server_version.startswith("0."):
                logger.warning(
                    f"SDK/API Version mismatch: SDK expects 0.x.x, Server is {server_version}. "
                    f"Compatibility is not guaranteed."
                )

            return response

        resp = await _async_retry(_op, self.max_retries)
        return resp.json()  # type: ignore[str, Any]

    async def insert(self, request: MemoryInsertRequest) -> MemoryInsertResponse:
        """POST /v3/memory/insert - Insert memory into the MESA system asynchronously.

        Args:
            request: MemoryInsertRequest instance.
        Returns:
            MemoryInsertResponse instance.
        """
        try:
            payload = request.model_dump(mode="json")
            data = await self._request("POST", "/v3/memory/insert", json=payload)
            return MemoryInsertResponse(**data)
        except ValidationError as e:
            raise MesaValidationError(f"Response validation failed: {e}") from e

    async def search(self, request: MemorySearchRequest) -> MemorySearchResponse:
        """POST /v3/memory/search - Search memory in the MESA system asynchronously.

        Args:
            request: MemorySearchRequest instance.
        Returns:
            MemorySearchResponse instance.
        """
        try:
            payload = request.model_dump(mode="json")
            data = await self._request("POST", "/v3/memory/search", json=payload)
            return MemorySearchResponse(**data)
        except ValidationError as e:
            raise MesaValidationError(f"Response validation failed: {e}") from e

    async def purge(self, request: MemoryPurgeRequest) -> MemoryPurgeResponse:
        """DELETE /v3/memory/purge - Purge (soft-delete) memory in the MESA system asynchronously.

        Args:
            request: MemoryPurgeRequest instance.
        Returns:
            MemoryPurgeResponse instance.
        """
        try:
            payload = request.model_dump(mode="json")
            data = await self._request("DELETE", "/v3/memory/purge", json=payload)
            return MemoryPurgeResponse(**data)
        except ValidationError as e:
            raise MesaValidationError(f"Response validation failed: {e}") from e


_V4_TERMINAL_MUTATION_STATES = frozenset(
    {"COMMITTED", "REJECTED", "DEAD_LETTER", "ROLLED_BACK", "BLOCKED"}
)


class MesaV4Client(MesaClient):
    """Client for the breaking V4 full-cognitive lifecycle contract.

    V3 methods intentionally remain on :class:`MesaClient`.  A separate
    class prevents an application silently mixing a lexical-core insert with
    V4 mutation polling.
    """

    def create_workspace(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        tenant_name: str | None = None,
        workspace_name: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v4/catalog/workspaces",
            json={
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "tenant_name": tenant_name,
                "workspace_name": workspace_name,
            },
        )

    def list_workspaces(self, *, tenant_id: str) -> dict[str, Any]:
        return self._request(
            "GET", "/v4/catalog/workspaces", params={"tenant_id": tenant_id}
        )

    def create_dataset(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
        tenant_name: str | None = None,
        workspace_name: str | None = None,
        dataset_name: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v4/catalog/datasets",
            json={
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "dataset_id": dataset_id,
                "tenant_name": tenant_name,
                "workspace_name": workspace_name,
                "dataset_name": dataset_name,
            },
        )

    def list_datasets(self, *, tenant_id: str, workspace_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            "/v4/catalog/datasets",
            params={"tenant_id": tenant_id, "workspace_id": workspace_id},
        )

    def create_document(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
        document_id: str,
        title: str,
        external_ref: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v4/catalog/documents",
            json={
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "dataset_id": dataset_id,
                "document_id": document_id,
                "title": title,
                "external_ref": external_ref,
            },
        )

    def list_documents(
        self, *, tenant_id: str, workspace_id: str, dataset_id: str
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/v4/catalog/documents",
            params={
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "dataset_id": dataset_id,
            },
        )

    def create_revision(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
        document_id: str,
        revision_id: str,
        revision_number: int,
        content_sha256: str,
        supersedes_revision_id: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v4/catalog/revisions",
            json={
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "dataset_id": dataset_id,
                "document_id": document_id,
                "revision_id": revision_id,
                "revision_number": revision_number,
                "content_sha256": content_sha256,
                "supersedes_revision_id": supersedes_revision_id,
            },
        )

    def list_revisions(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
        document_id: str,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/v4/catalog/revisions",
            params={
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "dataset_id": dataset_id,
                "document_id": document_id,
            },
        )

    def start_session(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        dataset_ids: list[str],
        agent_id: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v4/sessions/start",
            json={
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "dataset_ids": dataset_ids,
                "agent_id": agent_id,
            },
        )

    def insert(  # type: ignore[override]
        self,
        *,
        session_id: str,
        dataset_id: str,
        document_id: str,
        revision_id: str,
        chunk_id: str,
        title: str,
        source_ref: str,
        content: str,
        evidence_span: str = "",
        revision_number: int = 1,
        chunk_ordinal: int = 0,
        supersedes_revision_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v4/memory/insert",
            json={
                "session_id": session_id,
                "dataset_id": dataset_id,
                "document_id": document_id,
                "revision_id": revision_id,
                "chunk_id": chunk_id,
                "title": title,
                "source_ref": source_ref,
                "content": content,
                "evidence_span": evidence_span,
                "revision_number": revision_number,
                "chunk_ordinal": chunk_ordinal,
                "supersedes_revision_id": supersedes_revision_id,
                "metadata": metadata or {},
            },
        )

    def search(  # type: ignore[override]
        self,
        *,
        session_id: str,
        query: str,
        dataset_ids: list[str] | None = None,
        limit: int = 10,
        jurisdiction: str | None = None,
        valid_at: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v4/memory/search",
            json={
                "session_id": session_id,
                "dataset_ids": dataset_ids,
                "query": query,
                "limit": limit,
                "jurisdiction": jurisdiction,
                "valid_at": valid_at,
            },
        )

    def status(self, mutation_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v4/mutations/{mutation_id}")

    def wait_until_committed(
        self,
        mutation_id: str,
        *,
        timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 0.25,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            status = self.status(mutation_id)
            if status.get("state") in _V4_TERMINAL_MUTATION_STATES:
                return status
            if time.monotonic() >= deadline:
                raise MesaNetworkError(f"Timed out waiting for mutation {mutation_id}")
            time.sleep(poll_interval_seconds)

    def rollback(self, mutation_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v4/mutations/{mutation_id}/rollback")

    def replay(self, mutation_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v4/mutations/{mutation_id}/replay")

    def purge_document(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
        document_id: str,
    ) -> dict[str, Any]:
        return self._request(
            "DELETE",
            f"/v4/catalog/documents/{document_id}",
            params={
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "dataset_id": dataset_id,
            },
        )

    def end_session(self, *, session_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v4/sessions/{session_id}/end")

    def get_context(self, *, session_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v4/sessions/{session_id}/context")


class AsyncMesaV4Client(AsyncMesaClient):
    """Async counterpart of :class:`MesaV4Client`."""

    async def create_workspace(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        tenant_name: str | None = None,
        workspace_name: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v4/catalog/workspaces",
            json={
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "tenant_name": tenant_name,
                "workspace_name": workspace_name,
            },
        )

    async def list_workspaces(self, *, tenant_id: str) -> dict[str, Any]:
        return await self._request(
            "GET", "/v4/catalog/workspaces", params={"tenant_id": tenant_id}
        )

    async def create_dataset(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
        tenant_name: str | None = None,
        workspace_name: str | None = None,
        dataset_name: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v4/catalog/datasets",
            json={
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "dataset_id": dataset_id,
                "tenant_name": tenant_name,
                "workspace_name": workspace_name,
                "dataset_name": dataset_name,
            },
        )

    async def list_datasets(
        self, *, tenant_id: str, workspace_id: str
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/v4/catalog/datasets",
            params={"tenant_id": tenant_id, "workspace_id": workspace_id},
        )

    async def create_document(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
        document_id: str,
        title: str,
        external_ref: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v4/catalog/documents",
            json={
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "dataset_id": dataset_id,
                "document_id": document_id,
                "title": title,
                "external_ref": external_ref,
            },
        )

    async def list_documents(
        self, *, tenant_id: str, workspace_id: str, dataset_id: str
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/v4/catalog/documents",
            params={
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "dataset_id": dataset_id,
            },
        )

    async def create_revision(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
        document_id: str,
        revision_id: str,
        revision_number: int,
        content_sha256: str,
        supersedes_revision_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v4/catalog/revisions",
            json={
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "dataset_id": dataset_id,
                "document_id": document_id,
                "revision_id": revision_id,
                "revision_number": revision_number,
                "content_sha256": content_sha256,
                "supersedes_revision_id": supersedes_revision_id,
            },
        )

    async def list_revisions(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
        document_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/v4/catalog/revisions",
            params={
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "dataset_id": dataset_id,
                "document_id": document_id,
            },
        )

    async def start_session(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        dataset_ids: list[str],
        agent_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v4/sessions/start",
            json={
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "dataset_ids": dataset_ids,
                "agent_id": agent_id,
            },
        )

    async def insert(  # type: ignore[override]
        self,
        *,
        session_id: str,
        dataset_id: str,
        document_id: str,
        revision_id: str,
        chunk_id: str,
        title: str,
        source_ref: str,
        content: str,
        evidence_span: str = "",
        revision_number: int = 1,
        chunk_ordinal: int = 0,
        supersedes_revision_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v4/memory/insert",
            json={
                "session_id": session_id,
                "dataset_id": dataset_id,
                "document_id": document_id,
                "revision_id": revision_id,
                "chunk_id": chunk_id,
                "title": title,
                "source_ref": source_ref,
                "content": content,
                "evidence_span": evidence_span,
                "revision_number": revision_number,
                "chunk_ordinal": chunk_ordinal,
                "supersedes_revision_id": supersedes_revision_id,
                "metadata": metadata or {},
            },
        )

    async def search(  # type: ignore[override]
        self,
        *,
        session_id: str,
        query: str,
        dataset_ids: list[str] | None = None,
        limit: int = 10,
        jurisdiction: str | None = None,
        valid_at: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v4/memory/search",
            json={
                "session_id": session_id,
                "dataset_ids": dataset_ids,
                "query": query,
                "limit": limit,
                "jurisdiction": jurisdiction,
                "valid_at": valid_at,
            },
        )

    async def status(self, mutation_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v4/mutations/{mutation_id}")

    async def wait_until_committed(
        self,
        mutation_id: str,
        *,
        timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 0.25,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            status = await self.status(mutation_id)
            if status.get("state") in _V4_TERMINAL_MUTATION_STATES:
                return status
            if time.monotonic() >= deadline:
                raise MesaNetworkError(f"Timed out waiting for mutation {mutation_id}")
            await asyncio.sleep(poll_interval_seconds)

    async def rollback(self, mutation_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/v4/mutations/{mutation_id}/rollback")

    async def replay(self, mutation_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/v4/mutations/{mutation_id}/replay")

    async def purge_document(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
        document_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "DELETE",
            f"/v4/catalog/documents/{document_id}",
            params={
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "dataset_id": dataset_id,
            },
        )

    async def end_session(self, *, session_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/v4/sessions/{session_id}/end")

    async def get_context(self, *, session_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v4/sessions/{session_id}/context")
