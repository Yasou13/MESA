"""Application composition for the offline V4 projection rebuild CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mesa_memory.adapter.factory import AdapterFactory
from mesa_memory.config import config, configured_embedding_identity
from mesa_storage.embedding_identity import (
    EmbeddingIdentityAdoptionError,
    adopt_legacy_embedding_identity,
)
from mesa_storage.projection_generations import ProjectionGenerationRepository
from mesa_storage.rebuild_cutover import (
    ParityGatedActivator,
    default_graph_verification_factory,
    default_vector_verification_factory,
)
from mesa_storage.rebuild_observability import log_rebuild_event
from mesa_storage.rebuild_preparation import (
    OfflineRebuildPreparer,
    RebuildPreparationError,
    resume_cutover_preparation,
)
from mesa_storage.rebuild_replay import (
    ProjectionReplayer,
    ProjectionSnapshot,
    RebuildInterruptedError,
    RebuildReplayResult,
)
from mesa_storage.repositories.operations import (
    OperationFencedError,
    OperationNotFoundError,
    OperationRepository,
    OperationStateError,
)
from mesa_storage.sqlite_engine import AsyncEngine
from mesa_storage.vector_engine import EmbeddingProvider
from mesa_storage.writer_lock import (
    StorageWriterLock,
    StorageWriterLockError,
)

EXIT_OK = 0
EXIT_CONFIGURATION = 2
EXIT_RETRYABLE = 3
EXIT_WRITER_ACTIVE = 4
EXIT_FINAL = 5


@dataclass(frozen=True)
class RebuildProviderRuntime:
    manifest: dict[str, Any]
    embedding_provider: EmbeddingProvider | None
    allow_model_loading: bool
    local_embedding_model: str


def _environment_bool(name: str, *, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be an explicit boolean")


def _provider_runtime() -> RebuildProviderRuntime:
    external = _environment_bool("MESA_EXTERNAL_PROVIDER_ENABLED")
    model_enabled = _environment_bool("MESA_MODEL_ENABLED")
    embedding_provider: EmbeddingProvider | None = None
    if external:
        adapter = AdapterFactory.get_adapter()
        embedding_provider = adapter.aembed
    identity = configured_embedding_identity()
    return RebuildProviderRuntime(
        manifest={
            "embedding_provider": identity.provider,
            "embedding_model": identity.model,
            "embedding_version": identity.version,
            "dimension": identity.dimension,
        },
        embedding_provider=embedding_provider,
        allow_model_loading=model_enabled,
        local_embedding_model=config.local_embedding_model,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mesa-v4-rebuild")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--trusted-root", type=Path, required=True)
    run.add_argument("--storage-root", type=Path, required=True)
    run.add_argument("--work-root", type=Path, required=True)
    run.add_argument("--operation-id", required=True)
    run.add_argument("--batch-size", type=int, default=100)
    run.add_argument("--lease-seconds", type=int, default=300)
    adopt = subparsers.add_parser(
        "adopt-provider",
        help="Explicitly adopt missing legacy vector provider provenance offline",
    )
    adopt.add_argument("--trusted-root", type=Path, required=True)
    adopt.add_argument("--storage-root", type=Path, required=True)
    adopt.add_argument("--provider", required=True)
    adopt.add_argument("--model", required=True)
    adopt.add_argument("--version", required=True)
    adopt.add_argument("--dimension", type=int, required=True)
    adopt.add_argument(
        "--confirm-legacy-provider-unknown",
        action="store_true",
        help="Confirm that the operator verified the legacy provider externally",
    )
    return parser


def run_provider_adoption(args: argparse.Namespace) -> int:
    if not config.v4_rebuild_enabled or not args.confirm_legacy_provider_unknown:
        print(
            json.dumps({"status": "rejected", "error_class": "AdoptionNotConfirmed"}),
            file=sys.stderr,
        )
        return EXIT_CONFIGURATION
    try:
        with StorageWriterLock.acquire(
            args.storage_root, owner="v4-provider-adoption"
        ) as writer_lock:
            updated = adopt_legacy_embedding_identity(
                trusted_root=args.trusted_root,
                storage_root=args.storage_root,
                writer_lock=writer_lock,
                provider=args.provider,
                model=args.model,
                version=args.version,
                dimension=args.dimension,
            )
    except (EmbeddingIdentityAdoptionError, StorageWriterLockError, OSError) as exc:
        print(
            json.dumps({"status": "rejected", "error_class": type(exc).__name__}),
            file=sys.stderr,
        )
        return EXIT_CONFIGURATION
    print(json.dumps({"status": "adopted", "updated_mutations": updated}))
    return EXIT_OK


async def _mark_operation_failure(
    operations: OperationRepository,
    operation_id: str,
    *,
    runner_id: str,
    error_class: str,
) -> str | None:
    operation = await operations.get(operation_id)
    if operation is None:
        return None
    current_state = str(operation["state"])
    if current_state in {
        "RETRYABLE_FAILED",
        "FINAL_FAILED",
        "CANCELLED",
        "COMPLETED",
    }:
        return current_state
    if operation.get("claimed_by") != runner_id or not operation.get("claim_token"):
        return None
    attempt_count = int(operation.get("attempt_count", 0))
    retry_limit = int(operation.get("retry_limit", 3))
    target_state = (
        "FINAL_FAILED" if attempt_count >= retry_limit else "RETRYABLE_FAILED"
    )
    checkpoint = dict(operation.get("checkpoint") or {})
    checkpoint["phase"] = target_state
    try:
        transitioned = await operations.transition(
            operation_id,
            to_state=target_state,
            runner_id=runner_id,
            claim_token=str(operation["claim_token"]),
            fencing_token=int(operation["fencing_token"]),
            progress_completed=int(operation["progress_completed"]),
            progress_total=int(operation["progress_total"]),
            checkpoint=checkpoint,
            error_class=error_class[:128],
        )
    except OperationFencedError:
        # A concurrent fence owner remains authoritative.
        return None
    except OperationStateError:
        latest = await operations.get(operation_id)
        if latest is not None and str(latest["state"]) in {
            "RETRYABLE_FAILED",
            "FINAL_FAILED",
            "CANCELLED",
            "COMPLETED",
        }:
            return str(latest["state"])
        raise
    return str(transitioned["state"])


async def run_rebuild(args: argparse.Namespace) -> int:
    started_at = time.monotonic()
    if not config.v4_rebuild_enabled:
        log_rebuild_event(
            "rejected",
            operation_id=args.operation_id,
            error_class="FeatureDisabled",
            level="warning",
        )
        print(
            json.dumps({"status": "disabled", "error_class": "FeatureDisabled"}),
            file=sys.stderr,
        )
        return EXIT_CONFIGURATION
    if not 1 <= args.batch_size <= 1000 or not 30 <= args.lease_seconds <= 3600:
        log_rebuild_event(
            "rejected",
            operation_id=args.operation_id,
            error_class="InvalidBounds",
            level="warning",
        )
        print(
            json.dumps({"status": "rejected", "error_class": "InvalidBounds"}),
            file=sys.stderr,
        )
        return EXIT_CONFIGURATION

    runner_id = f"v4-rebuild-{os.getpid()}"
    stop_requested = asyncio.Event()
    loop = asyncio.get_running_loop()
    signal_installed = False
    try:
        loop.add_signal_handler(signal.SIGTERM, stop_requested.set)
        signal_installed = True
    except (NotImplementedError, RuntimeError):
        pass

    writer_lock: StorageWriterLock | None = None
    engine: AsyncEngine | None = None
    operations: OperationRepository | None = None
    try:
        writer_lock = StorageWriterLock.acquire(
            args.storage_root, owner="v4-rebuild-runner"
        )
        engine = AsyncEngine(str(args.storage_root / "mesa.db"), max_connections=2)
        await engine.initialize()
        operations = OperationRepository(engine)
        generations = ProjectionGenerationRepository(engine)
        existing = await operations.get(args.operation_id)
        if existing is None:
            raise OperationNotFoundError("operation is unavailable")
        claimed = await operations.claim(
            args.operation_id,
            runner_id=runner_id,
            lease_seconds=args.lease_seconds,
        )
        log_rebuild_event(
            "claimed",
            operation_id=args.operation_id,
            state=str(claimed["state"]),
            progress_completed=int(claimed["progress_completed"]),
            progress_total=int(claimed["progress_total"]),
        )
        providers = _provider_runtime()
        recovering_cutover = claimed["state"] == "READY_TO_CUTOVER"
        if recovering_cutover:
            preparation = await resume_cutover_preparation(
                trusted_root=args.trusted_root,
                storage_root=args.storage_root,
                work_root=args.work_root,
                operation=claimed,
                generations=generations,
                writer_lock=writer_lock,
            )
        else:
            preparation = await OfflineRebuildPreparer(operations, generations).prepare(
                trusted_root=args.trusted_root,
                storage_root=args.storage_root,
                work_root=args.work_root,
                operation=claimed,
                runner_id=runner_id,
                writer_lock=writer_lock,
                provider_manifest=providers.manifest,
            )
        log_rebuild_event(
            "prepared",
            operation_id=args.operation_id,
            state=str(preparation.operation["state"]),
            generation=preparation.target_generation_id,
        )
        if recovering_cutover:
            counts = ProjectionSnapshot(preparation.backup_root / "mesa.db").counts()
            total = sum(counts.values())
            if total != int(claimed["progress_total"]):
                raise RebuildPreparationError(
                    "cutover replay total does not match the durable snapshot"
                )
            replay = RebuildReplayResult(
                operation=claimed,
                counts=counts,
                completed=total,
                total=total,
            )
        else:
            replay = await ProjectionReplayer(operations).replay(
                preparation=preparation,
                trusted_root=args.trusted_root,
                storage_root=args.storage_root,
                runner_id=runner_id,
                provider_manifest=providers.manifest,
                batch_size=args.batch_size,
                lease_seconds=args.lease_seconds,
                embedding_provider=providers.embedding_provider,
                allow_model_loading=providers.allow_model_loading,
                local_embedding_model=providers.local_embedding_model,
                should_stop=stop_requested.is_set,
            )
        log_rebuild_event(
            "replayed",
            operation_id=args.operation_id,
            state=str(replay.operation["state"]),
            generation=preparation.target_generation_id,
            progress_completed=replay.completed,
            progress_total=replay.total,
        )
        if stop_requested.is_set():
            raise RebuildInterruptedError(
                "rebuild interrupted after durable replay checkpoint"
            )
        vector_factory = default_vector_verification_factory(
            embedding_provider=providers.embedding_provider,
            allow_model_loading=providers.allow_model_loading,
            local_embedding_model=providers.local_embedding_model,
        )
        result = await ParityGatedActivator(operations, generations).activate(
            preparation=preparation,
            replay=replay,
            trusted_root=args.trusted_root,
            storage_root=args.storage_root,
            runner_id=runner_id,
            vector_factory=vector_factory,
            graph_factory=default_graph_verification_factory,
            lease_seconds=args.lease_seconds,
            should_stop=stop_requested.is_set,
        )
        log_rebuild_event(
            "completed",
            operation_id=args.operation_id,
            state=str(result.operation["state"]),
            generation=result.active_generation_id,
            progress_completed=int(result.operation["progress_completed"]),
            progress_total=int(result.operation["progress_total"]),
            duration_seconds=time.monotonic() - started_at,
        )
        print(
            json.dumps(
                {
                    "operation_id": args.operation_id,
                    "state": result.operation["state"],
                    "active_generation_id": result.active_generation_id,
                    "retained_generation_id": result.retained_generation_id,
                },
                sort_keys=True,
            )
        )
        return EXIT_OK
    except StorageWriterLockError:
        log_rebuild_event(
            "writer_blocked",
            operation_id=args.operation_id,
            error_class="WriterActive",
            duration_seconds=time.monotonic() - started_at,
            level="warning",
        )
        print(
            json.dumps({"status": "blocked", "error_class": "WriterActive"}),
            file=sys.stderr,
        )
        return EXIT_WRITER_ACTIVE
    except (OperationNotFoundError, OperationStateError, ValueError) as exc:
        failure_state: str | None = None
        if operations is not None:
            failure_state = await _mark_operation_failure(
                operations,
                args.operation_id,
                runner_id=runner_id,
                error_class=type(exc).__name__,
            )
        if failure_state == "FINAL_FAILED":
            log_rebuild_event(
                "failed",
                operation_id=args.operation_id,
                state=failure_state,
                error_class=type(exc).__name__,
                duration_seconds=time.monotonic() - started_at,
                level="error",
            )
            print(
                json.dumps(
                    {"status": "final_failed", "error_class": type(exc).__name__}
                ),
                file=sys.stderr,
            )
            return EXIT_FINAL
        log_rebuild_event(
            "rejected",
            operation_id=args.operation_id,
            error_class=type(exc).__name__,
            duration_seconds=time.monotonic() - started_at,
            level="warning",
        )
        print(
            json.dumps({"status": "rejected", "error_class": type(exc).__name__}),
            file=sys.stderr,
        )
        return EXIT_CONFIGURATION
    except Exception as exc:
        failure_state = None
        if operations is not None:
            failure_state = await _mark_operation_failure(
                operations,
                args.operation_id,
                runner_id=runner_id,
                error_class=type(exc).__name__,
            )
        final = failure_state == "FINAL_FAILED"
        reported_state = "FINAL_FAILED" if final else "RETRYABLE_FAILED"
        log_rebuild_event(
            "failed",
            operation_id=args.operation_id,
            state=reported_state,
            error_class=type(exc).__name__,
            duration_seconds=time.monotonic() - started_at,
            level="error",
        )
        print(
            json.dumps(
                {
                    "status": "final_failed" if final else "retryable_failed",
                    "error_class": type(exc).__name__,
                }
            ),
            file=sys.stderr,
        )
        return EXIT_FINAL if final else EXIT_RETRYABLE
    finally:
        if engine is not None:
            await engine.close()
        if writer_lock is not None:
            writer_lock.release()
        if signal_installed:
            loop.remove_signal_handler(signal.SIGTERM)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "adopt-provider":
        return run_provider_adoption(args)
    return asyncio.run(run_rebuild(args))


if __name__ == "__main__":
    raise SystemExit(main())
