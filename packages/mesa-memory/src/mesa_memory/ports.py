"""Consumer-owned storage interfaces for MESA application modules.

These Protocols describe cohesive capabilities rather than database tables.
Concrete SQLite/LanceDB/Kuzu adapters live in :mod:`mesa_storage`.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


def supports_capability(value: object, protocol: type[Any]) -> bool:
    """Require an explicit storage marker as well as structural conformance.

    The marker prevents broad mocks and unrelated duck types from accidentally
    activating durable mutation paths merely because they expose similarly
    named attributes.
    """
    return (
        getattr(value, "storage_capability_version", None) == 1
        and isinstance(value, protocol)
    )


class CatalogStore(Protocol):
    async def create_workspace(self, **identity: Any) -> dict[str, Any]: ...

    async def list_workspaces(self, *, tenant_id: str) -> list[dict[str, Any]]: ...

    async def ensure_scope(self, **identity: Any) -> None: ...


@runtime_checkable
class MutationLedger(Protocol):
    async def admit_v4_memory(self, **record: Any) -> dict[str, Any]: ...

    async def get_mutation_summary(
        self, mutation_id: str
    ) -> dict[str, Any] | None: ...

    async def set_mutation_state(
        self, mutation_id: str, state: str, **outcome: Any
    ) -> None: ...

    async def transition_pipeline_run(
        self, pipeline_run_id: str, state: str, **outcome: Any
    ) -> None: ...


@runtime_checkable
class ProjectionStore(Protocol):
    async def get_projection_mutation(
        self, mutation_id: str
    ) -> dict[str, Any] | None: ...

    async def project_v4_sql_entity(self, **projection: Any) -> Any: ...

    async def project_v4_vector_entity(self, **projection: Any) -> Any: ...

    async def project_v4_graph_triplet(self, **projection: Any) -> Any: ...

    async def claim_projection_outbox(
        self, *, worker_id: str, limit: int
    ) -> list[dict[str, Any]]: ...

    async def complete_projection_outbox(
        self,
        projection_id: str,
        *,
        worker_id: str,
        claim_token: str,
        outcome: str,
    ) -> bool: ...

    async def fail_projection_outbox(
        self,
        projection_id: str,
        *,
        worker_id: str,
        claim_token: str,
        error_class: str,
        retryable: bool,
    ) -> bool: ...

    async def renew_projection_outbox_lease(
        self,
        projection_id: str,
        *,
        worker_id: str,
        claim_token: str,
        lease_seconds: int = 300,
    ) -> bool: ...

    async def claim_artifact_cleanup(
        self, *, worker_id: str, limit: int = 1, lease_seconds: int = 300
    ) -> list[dict[str, Any]]: ...

    async def apply_artifact_cleanup(self, cleanup: dict[str, Any]) -> None: ...

    async def finish_artifact_cleanup(
        self,
        cleanup_id: str,
        *,
        worker_id: str,
        claim_token: str,
        error_class: str | None = None,
    ) -> bool: ...

    async def get_pipeline_run(
        self, pipeline_run_id: str
    ) -> dict[str, Any] | None: ...


@runtime_checkable
class IngestionQueue(Protocol):
    async def admit_raw_log(self, **record: Any) -> dict[str, Any]: ...

    async def claim_raw_log(
        self, agent_id: str, log_id: int, **claim: Any
    ) -> dict[str, Any] | None: ...

    async def request_session_finalization(
        self, agent_id: str, session_id: str, **request: Any
    ) -> dict[str, Any]: ...

    async def claim_dispatch_queue(
        self, *, worker_id: str, limit: int
    ) -> list[dict[str, Any]]: ...


class LegacyMemoryStore(Protocol):
    async def insert_memory(self, agent_id: str, **memory: Any) -> str: ...

    async def search_memory(
        self, agent_id: str, **query: Any
    ) -> list[dict[str, Any]]: ...

    async def search_memory_fts(
        self, agent_id: str, query: str, **options: Any
    ) -> list[dict[str, Any]]: ...


class PurgeCoordinator(Protocol):
    async def purge_memory(
        self, agent_id: str, *, scope: str, **request: Any
    ) -> dict[str, Any]: ...

    async def resume_purge(
        self, agent_id: str, purge_id: str
    ) -> dict[str, Any]: ...
