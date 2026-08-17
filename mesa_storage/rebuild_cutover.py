"""Parity-gated projection activation with automatic retained rollback."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, cast

from mesa_storage.kuzu_provider import KuzuGraphProvider
from mesa_storage.projection_generations import (
    ProjectionGenerationRepositoryPort,
    ProjectionPaths,
    resolve_projection_generation_paths,
)
from mesa_storage.rebuild_preparation import RebuildPreparation
from mesa_storage.rebuild_replay import (
    GraphReplayTarget,
    ProjectionSnapshot,
    RebuildReplayResult,
    VectorReplayTarget,
)
from mesa_storage.repositories.operations import OperationRepositoryPort
from mesa_storage.retrieval_scope import scope_vector_result_ids
from mesa_storage.vector_engine import EmbeddingProvider, VectorEngine


class VectorVerificationTarget(VectorReplayTarget, Protocol):
    async def health_check(self) -> dict[str, Any]: ...

    async def count_records(self, active_only: bool = True) -> dict[str, int]: ...

    async def get_existing_node_ids(
        self, agent_id: str, node_ids: list[str]
    ) -> set[str]: ...

    async def search(
        self,
        query_vector: list[float],
        *,
        limit: int = 10,
        agent_id: str | None = None,
        allowed_node_ids: set[str] | None = None,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]: ...


class GraphVerificationTarget(GraphReplayTarget, Protocol):
    async def health_check(self) -> dict[str, Any]: ...

    async def get_existing_node_ids(
        self, label: str, agent_id: str, node_ids: list[str]
    ) -> set[str]: ...

    async def execute_query(
        self, query: str, parameters: dict[str, Any] | None = None
    ) -> list[Any]: ...


class RebuildVerificationError(RuntimeError):
    """A staging or activated generation failed content-free verification."""


class ProjectionParityError(RebuildVerificationError):
    """Expected and rebuilt projection artifacts differ."""

    def __init__(self, report: "ProjectionParityReport") -> None:
        super().__init__("projection parity verification failed")
        self.report = report


class PostCutoverRollbackError(RebuildVerificationError):
    """Post-cutover recovery could not prove the retained generation healthy."""


@dataclass(frozen=True)
class ProjectionParityReport:
    expected_vector: int
    expected_graph_entities: int
    expected_graph_assertions: int
    expected_graph_links: int
    actual_vector: int
    actual_graph_entities: int
    actual_graph_assertions: int
    actual_graph_links: int
    missing: int
    orphans: int
    smoke_checked: int
    cross_dataset_checked: int

    @property
    def passed(self) -> bool:
        return self.missing == 0 and self.orphans == 0

    def checkpoint(self) -> dict[str, int | bool]:
        return {"passed": self.passed, **asdict(self)}


@dataclass(frozen=True)
class RebuildCutoverResult:
    operation: dict[str, Any]
    parity: ProjectionParityReport
    active_generation_id: str
    retained_generation_id: str


def _count_row(rows: list[Any]) -> int:
    if not rows or not rows[0]:
        raise RebuildVerificationError("graph count probe returned no result")
    return int(rows[0][0])


class ProjectionParityVerifier:
    """Verify bounded identities, exact counts, health and dataset isolation."""

    async def verify(
        self,
        *,
        snapshot: ProjectionSnapshot,
        paths: ProjectionPaths,
        vector_factory: Callable[[Path], VectorVerificationTarget],
        graph_factory: Callable[[Path], GraphVerificationTarget],
        parity_limit: int = 500,
        smoke_limit: int = 10,
    ) -> ProjectionParityReport:
        if not 1 <= parity_limit <= 10_000 or not 1 <= smoke_limit <= 100:
            raise ValueError("invalid projection verification bounds")
        vector = vector_factory(paths.vector_path)
        graph = graph_factory(paths.graph_path)
        try:
            if Path(vector.uri).resolve(strict=False) != paths.vector_path:
                raise RebuildVerificationError(
                    "vector verification target is not active"
                )
            if Path(graph.db_path).resolve(strict=False) != paths.graph_path:
                raise RebuildVerificationError(
                    "graph verification target is not active"
                )
            await vector.initialize()
            await graph.initialize()
            vector_health = await vector.health_check()
            graph_health = await graph.health_check()
            if (
                vector_health.get("status") != "healthy"
                or graph_health.get("status") != "healthy"
            ):
                raise RebuildVerificationError("projection provider health failed")

            expected = snapshot.counts()
            vector_counts = await vector.count_records(active_only=True)
            if any(count < 0 for count in vector_counts.values()):
                raise RebuildVerificationError("vector count probe failed")
            actual_vector = sum(vector_counts.values())
            actual_graph_entities = _count_row(
                await graph.execute_query("MATCH (n:Entity) RETURN COUNT(n)")
            )
            actual_graph_assertions = _count_row(
                await graph.execute_query("MATCH (n:Assertion) RETURN COUNT(n)")
            )
            actual_graph_links = _count_row(
                await graph.execute_query(
                    "MATCH (:Assertion)-[r:AssertionLink]->(:Assertion) RETURN COUNT(r)"
                )
            )

            missing_ids = 0
            for lane in ("vector", "graph_entity", "graph_assertion"):
                identifier_key = (
                    "entity_id" if lane == "graph_entity" else "assertion_id"
                )
                # Exact parity can be bounded in memory without being silently
                # truncated: iterate every snapshot identity in fixed chunks.
                for offset in range(0, expected[lane], parity_limit):
                    rows = snapshot.fetch(lane, offset=offset, limit=parity_limit)
                    by_agent: dict[str, list[str]] = {}
                    for row in rows:
                        by_agent.setdefault(str(row["agent_id"]), []).append(
                            str(row[identifier_key])
                        )
                    for agent_id, identifiers in by_agent.items():
                        if lane == "vector":
                            found = await vector.get_existing_node_ids(
                                agent_id, identifiers
                            )
                        else:
                            label = "Entity" if lane == "graph_entity" else "Assertion"
                            found = await graph.get_existing_node_ids(
                                label, agent_id, identifiers
                            )
                        missing_ids += len(set(identifiers) - found)

            smoke_checked = 0
            cross_dataset_checked = 0
            for case in snapshot.vector_smoke_cases(limit=smoke_limit):
                embeddings = await vector.compute_embedding_batch(
                    [str(case["payload_text"])]
                )
                if len(embeddings) != 1:
                    raise RebuildVerificationError("retrieval smoke embedding failed")
                allowed = snapshot.allowed_vector_ids(
                    tenant_id=str(case["tenant_id"]),
                    agent_id=str(case["agent_id"]),
                    dataset_id=str(case["dataset_id"]),
                )
                results = await vector.search(
                    embeddings[0],
                    limit=50,
                    agent_id=str(case["agent_id"]),
                    allowed_node_ids=allowed,
                )
                raw_result_ids = {str(row.get("node_id", "")) for row in results}
                if not raw_result_ids.issubset(allowed):
                    raise RebuildVerificationError("retrieval scope smoke failed")
                result_ids = set(scope_vector_result_ids(results, allowed_ids=allowed))
                vector_id = str(case["assertion_id"])
                if vector_id not in result_ids:
                    missing_ids += 1
                smoke_checked += 1
                for other_tenant, other_dataset in snapshot.retrieval_scopes(
                    agent_id=str(case["agent_id"])
                ):
                    if other_tenant == str(case["tenant_id"]) and other_dataset == str(
                        case["dataset_id"]
                    ):
                        continue
                    other_allowed = snapshot.allowed_vector_ids(
                        tenant_id=other_tenant,
                        agent_id=str(case["agent_id"]),
                        dataset_id=other_dataset,
                    )
                    if vector_id not in other_allowed:
                        other_results = await vector.search(
                            embeddings[0],
                            limit=50,
                            agent_id=str(case["agent_id"]),
                            allowed_node_ids=other_allowed,
                        )
                        other_result_ids = {
                            str(row.get("node_id", "")) for row in other_results
                        }
                        if not other_result_ids.issubset(other_allowed):
                            raise RebuildVerificationError(
                                "retrieval scope smoke failed"
                            )
                        if vector_id in other_result_ids:
                            raise RebuildVerificationError(
                                "cross-dataset retrieval smoke failed"
                            )
                        cross_dataset_checked += 1
                        break

            expected_counts = (
                expected["vector"],
                expected["graph_entity"],
                expected["graph_assertion"],
                expected["graph_link"],
            )
            actual_counts = (
                actual_vector,
                actual_graph_entities,
                actual_graph_assertions,
                actual_graph_links,
            )
            missing_counts = sum(
                max(wanted - actual, 0)
                for wanted, actual in zip(expected_counts, actual_counts)
            )
            orphan_counts = sum(
                max(actual - wanted, 0)
                for wanted, actual in zip(expected_counts, actual_counts)
            )
            report = ProjectionParityReport(
                expected_vector=expected["vector"],
                expected_graph_entities=expected["graph_entity"],
                expected_graph_assertions=expected["graph_assertion"],
                expected_graph_links=expected["graph_link"],
                actual_vector=actual_vector,
                actual_graph_entities=actual_graph_entities,
                actual_graph_assertions=actual_graph_assertions,
                actual_graph_links=actual_graph_links,
                missing=missing_counts + missing_ids,
                orphans=orphan_counts,
                smoke_checked=smoke_checked,
                cross_dataset_checked=cross_dataset_checked,
            )
            if not report.passed:
                raise ProjectionParityError(report)
            return report
        finally:
            await graph.close()
            await vector.close()


class ParityGatedActivator:
    """Activate exactly once after parity and roll back on post-cutover failure."""

    def __init__(
        self,
        operations: OperationRepositoryPort,
        generations: ProjectionGenerationRepositoryPort,
        verifier: ProjectionParityVerifier | None = None,
    ) -> None:
        self._operations = operations
        self._generations = generations
        self._verifier = verifier or ProjectionParityVerifier()

    async def activate(
        self,
        *,
        preparation: RebuildPreparation,
        replay: RebuildReplayResult,
        trusted_root: Path,
        storage_root: Path,
        runner_id: str,
        vector_factory: Callable[[Path], VectorVerificationTarget],
        graph_factory: Callable[[Path], GraphVerificationTarget],
        parity_limit: int = 500,
        smoke_limit: int = 10,
        lease_seconds: int = 300,
        should_stop: Callable[[], bool] | None = None,
    ) -> RebuildCutoverResult:
        operation = replay.operation
        operation_id = str(operation["operation_id"])
        claim_token = str(operation["claim_token"])
        fencing_token = int(operation["fencing_token"])
        paths = resolve_projection_generation_paths(
            preparation.generation,
            storage_root=storage_root,
            trusted_root=trusted_root,
            runtime_fencing_token=preparation.runtime_fencing_token,
        )
        snapshot = ProjectionSnapshot(preparation.backup_root / "mesa.db")
        if should_stop is not None and should_stop():
            await self._record_failure(
                operation,
                runner_id=runner_id,
                error_class="RebuildInterrupted",
            )
            raise RebuildVerificationError("rebuild interrupted before verification")
        await self._operations.renew(
            operation_id,
            runner_id=runner_id,
            claim_token=claim_token,
            fencing_token=fencing_token,
            lease_seconds=lease_seconds,
        )
        try:
            parity = await self._verifier.verify(
                snapshot=snapshot,
                paths=paths,
                vector_factory=vector_factory,
                graph_factory=graph_factory,
                parity_limit=parity_limit,
                smoke_limit=smoke_limit,
            )
        except Exception as exc:
            await self._record_failure(
                operation,
                runner_id=runner_id,
                error_class=(
                    "ProjectionParityMismatch"
                    if isinstance(exc, ProjectionParityError)
                    else "ProjectionVerificationFailed"
                ),
                parity_report=(
                    exc.report if isinstance(exc, ProjectionParityError) else None
                ),
            )
            raise RebuildVerificationError("pre-cutover verification failed") from exc

        if should_stop is not None and should_stop():
            await self._record_failure(
                operation,
                runner_id=runner_id,
                error_class="RebuildInterrupted",
            )
            raise RebuildVerificationError("rebuild interrupted before cutover")

        checkpoint = dict(operation.get("checkpoint") or {})
        checkpoint["phase"] = "READY_TO_CUTOVER"
        checkpoint["parity"] = parity.checkpoint()
        operation = await self._operations.transition(
            operation_id,
            to_state="READY_TO_CUTOVER",
            runner_id=runner_id,
            claim_token=claim_token,
            fencing_token=fencing_token,
            progress_completed=replay.total,
            progress_total=replay.total,
            checkpoint=checkpoint,
        )
        runtime = await self._generations.resolve_active(
            storage_root=storage_root,
            trusted_root=trusted_root,
        )
        if runtime.generation_id == preparation.source_generation_id:
            activated = await self._generations.activate(
                preparation.target_generation_id,
                operation_id=operation_id,
                runner_id=runner_id,
                claim_token=claim_token,
                operation_fencing_token=fencing_token,
                expected_active_generation_id=preparation.source_generation_id,
                runtime_fencing_token=runtime.runtime_fencing_token,
            )
            activated_fencing_token = int(activated["fencing_token"])
        elif (
            runtime.generation_id == preparation.target_generation_id
            and runtime.previous_generation_id == preparation.source_generation_id
        ):
            activated_fencing_token = runtime.runtime_fencing_token
        else:
            raise RebuildVerificationError(
                "runtime pointer is outside the recoverable cutover pair"
            )
        try:
            post_cutover = await self._verifier.verify(
                snapshot=snapshot,
                paths=ProjectionPaths(
                    generation_id=preparation.target_generation_id,
                    vector_path=paths.vector_path,
                    graph_path=paths.graph_path,
                    runtime_fencing_token=activated_fencing_token,
                    previous_generation_id=preparation.source_generation_id,
                ),
                vector_factory=vector_factory,
                graph_factory=graph_factory,
                parity_limit=parity_limit,
                smoke_limit=smoke_limit,
            )
        except Exception as exc:
            rolled_back = await self._generations.rollback(
                operation_id=operation_id,
                runner_id=runner_id,
                claim_token=claim_token,
                operation_fencing_token=fencing_token,
                expected_active_generation_id=preparation.target_generation_id,
                runtime_fencing_token=activated_fencing_token,
            )
            retained_paths = await self._generations.resolve_active(
                storage_root=storage_root, trusted_root=trusted_root
            )
            try:
                await self._verifier.verify(
                    snapshot=snapshot,
                    paths=retained_paths,
                    vector_factory=vector_factory,
                    graph_factory=graph_factory,
                    parity_limit=parity_limit,
                    smoke_limit=smoke_limit,
                )
            except Exception as rollback_exc:
                await self._record_failure(
                    operation,
                    runner_id=runner_id,
                    error_class="RollbackVerificationFailed",
                    rollback=True,
                )
                raise PostCutoverRollbackError(
                    "retained generation verification failed"
                ) from rollback_exc
            await self._record_failure(
                operation,
                runner_id=runner_id,
                error_class="PostCutoverVerificationFailed",
                rollback=True,
            )
            if rolled_back["active_generation_id"] != preparation.source_generation_id:
                raise PostCutoverRollbackError("retained generation rollback failed")
            raise RebuildVerificationError(
                "post-cutover verification failed and pointer was rolled back"
            ) from exc

        checkpoint["phase"] = "COMPLETED"
        checkpoint["post_cutover"] = post_cutover.checkpoint()
        operation = await self._operations.transition(
            operation_id,
            to_state="COMPLETED",
            runner_id=runner_id,
            claim_token=claim_token,
            fencing_token=fencing_token,
            progress_completed=replay.total,
            progress_total=replay.total,
            checkpoint=checkpoint,
        )
        return RebuildCutoverResult(
            operation=operation,
            parity=post_cutover,
            active_generation_id=preparation.target_generation_id,
            retained_generation_id=preparation.source_generation_id,
        )

    async def _record_failure(
        self,
        operation: dict[str, Any],
        *,
        runner_id: str,
        error_class: str,
        parity_report: ProjectionParityReport | None = None,
        rollback: bool = False,
    ) -> dict[str, Any]:
        checkpoint = dict(operation.get("checkpoint") or {})
        checkpoint["phase"] = "RETRYABLE_FAILED"
        if parity_report is not None:
            checkpoint["parity"] = parity_report.checkpoint()
        if rollback:
            checkpoint["rollback_count"] = int(checkpoint.get("rollback_count", 0)) + 1
        return await self._operations.transition(
            str(operation["operation_id"]),
            to_state="RETRYABLE_FAILED",
            runner_id=runner_id,
            claim_token=str(operation["claim_token"]),
            fencing_token=int(operation["fencing_token"]),
            progress_completed=int(operation["progress_completed"]),
            progress_total=int(operation["progress_total"]),
            checkpoint=checkpoint,
            error_class=error_class,
        )


def default_vector_verification_factory(
    *,
    embedding_provider: EmbeddingProvider | None,
    embedding_service: Any | None = None,
    allow_model_loading: bool,
    local_embedding_model: str = "magibu/embeddingmagibu-200m",
) -> Callable[[Path], VectorVerificationTarget]:
    return lambda path: VectorEngine(
        str(path),
        embedding_provider=embedding_provider,
        embedding_service=embedding_service,
        allow_model_loading=allow_model_loading,
        local_embedding_model=local_embedding_model,
    )


def default_graph_verification_factory(path: Path) -> GraphVerificationTarget:
    return cast(GraphVerificationTarget, KuzuGraphProvider(str(path)))
