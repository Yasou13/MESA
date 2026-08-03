"""Command-line orchestration for the offline V4 projection rebuild."""

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
from mesa_memory.config import config
from mesa_storage.projection_generations import ProjectionGenerationRepository
from mesa_storage.rebuild_cutover import (
    ParityGatedActivator,
    default_graph_verification_factory,
    default_vector_verification_factory,
)
from mesa_storage.rebuild_observability import log_rebuild_event
from mesa_storage.rebuild_preparation import OfflineRebuildPreparer
from mesa_storage.rebuild_replay import (
    ProjectionReplayer,
    RebuildInterruptedError,
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


@dataclass(frozen=True)
class RebuildProviderRuntime:
    manifest: dict[str, Any]
    embedding_provider: EmbeddingProvider | None
    allow_model_loading: bool


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
        model = config.llm_embedding_model_name
        provider = config.mesa_llm_provider
    else:
        model = config.local_embedding_model
        provider = "local"
    return RebuildProviderRuntime(
        manifest={
            "embedding_provider": provider,
            "embedding_model": model,
            "embedding_version": config.embedding_version,
            "dimension": config.embedding_dimension,
        },
        embedding_provider=embedding_provider,
        allow_model_loading=model_enabled,
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
    return parser


async def _mark_retryable_failure(
    operations: OperationRepository,
    operation_id: str,
    *,
    runner_id: str,
    error_class: str,
) -> None:
    operation = await operations.get(operation_id)
    if operation is None or operation["state"] in {
        "RETRYABLE_FAILED",
        "FINAL_FAILED",
        "CANCELLED",
        "COMPLETED",
    }:
        return
    if operation.get("claimed_by") != runner_id or not operation.get("claim_token"):
        return
    checkpoint = dict(operation.get("checkpoint") or {})
    checkpoint["phase"] = "RETRYABLE_FAILED"
    try:
        await operations.transition(
            operation_id,
            to_state="RETRYABLE_FAILED",
            runner_id=runner_id,
            claim_token=str(operation["claim_token"]),
            fencing_token=int(operation["fencing_token"]),
            progress_completed=int(operation["progress_completed"]),
            progress_total=int(operation["progress_total"]),
            checkpoint=checkpoint,
            error_class=error_class[:128],
        )
    except (OperationFencedError, OperationStateError):
        # A concurrent fence owner or cutover recovery remains authoritative.
        return


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
        if operations is not None:
            await _mark_retryable_failure(
                operations,
                args.operation_id,
                runner_id=runner_id,
                error_class=type(exc).__name__,
            )
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
        if operations is not None:
            await _mark_retryable_failure(
                operations,
                args.operation_id,
                runner_id=runner_id,
                error_class=type(exc).__name__,
            )
        log_rebuild_event(
            "failed",
            operation_id=args.operation_id,
            state="RETRYABLE_FAILED",
            error_class=type(exc).__name__,
            duration_seconds=time.monotonic() - started_at,
            level="error",
        )
        print(
            json.dumps(
                {"status": "retryable_failed", "error_class": type(exc).__name__}
            ),
            file=sys.stderr,
        )
        return EXIT_RETRYABLE
    finally:
        if engine is not None:
            await engine.close()
        if writer_lock is not None:
            writer_lock.release()
        if signal_installed:
            loop.remove_signal_handler(signal.SIGTERM)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return asyncio.run(run_rebuild(args))


if __name__ == "__main__":
    raise SystemExit(main())
