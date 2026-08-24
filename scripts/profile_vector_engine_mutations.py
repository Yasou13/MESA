"""Profile isolated VectorEngine workloads without touching user storage.

Examples::

    .venv/bin/python scripts/profile_vector_engine_mutations.py \
        --mode insert --operations 1000 --sample-every 100
    .venv/bin/python scripts/profile_vector_engine_mutations.py \
        --mode update --operations 5000 --disable-maintenance

The command creates a fresh temporary Lance directory by default and prints a
JSON report containing time-series resource data.  Run each workload once with
maintenance disabled and once with the default configuration when investigating
fragmentation or native-memory growth.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any, Callable

import psutil
import pyarrow as pa

from mesa_storage.vector_engine import VectorEngine


class _PipeWakeupLoop(asyncio.SelectorEventLoop):
    """Selector loop that uses an anonymous pipe instead of a socketpair."""

    def _make_self_pipe(self) -> None:
        self._ssock, self._csock = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        self._internal_fds += 1
        self._add_reader(self._ssock, self._read_from_self)

    def _read_from_self(self) -> None:
        while True:
            try:
                os.read(self._ssock, 4096)
            except BlockingIOError:
                return

    def _write_to_self(self) -> None:
        try:
            os.write(self._csock, b"\0")
        except BlockingIOError:
            pass

    def _close_self_pipe(self) -> None:
        self._remove_reader(self._ssock)
        os.close(self._ssock)
        os.close(self._csock)
        self._ssock = None
        self._csock = None


def _needs_pipe_wakeup_workaround() -> bool:
    """Return whether this process receives EPERM from socketpair wakeups."""
    probe = asyncio.new_event_loop()
    try:
        probe._csock.send(b"x")  # type: ignore[union-attr]
    except PermissionError:
        return True
    finally:
        probe.close()
    return False


def _install_pipe_wakeup_workaround() -> Callable[[], None]:
    """Work around this desktop sandbox blocking socketpair wakeups with EPERM.

    LanceDB's synchronous API submits work to an asyncio loop in a background
    thread.  CPython normally wakes that loop through a Unix socketpair.  This
    sandbox rejects that send with ``EPERM``, leaving every sync LanceDB call
    blocked.  This profiler-only workaround substitutes an anonymous pipe;
    production MESA never installs it.
    """
    if not _needs_pipe_wakeup_workaround():
        return lambda: None

    from lancedb.background_loop import LOOP

    loop = _PipeWakeupLoop()
    thread = __import__("threading").Thread(
        target=loop.run_forever, name="LanceDBPipeWakeup", daemon=True
    )
    thread.start()
    LOOP.loop = loop
    LOOP.thread = thread

    def cleanup() -> None:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=1)
        loop.close()

    return cleanup


def _percentile(samples: list[float], percentile: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile))
    return round(ordered[index], 3)


def _path_stats(root: Path) -> dict[str, int]:
    files = [path for path in root.rglob("*") if path.is_file()]
    return {
        "fragment_files": sum(1 for path in files if path.suffix == ".lance"),
        "transaction_files": sum(1 for path in files if "_transactions" in path.parts),
        "version_files": sum(1 for path in files if "_versions" in path.parts),
        "disk_bytes": sum(path.stat().st_size for path in files),
    }


def _lance_stats(engine: VectorEngine) -> dict[str, int | None]:
    """Inspect current logical state, distinct from retained physical files."""
    try:
        assert engine._db is not None
        active_fragments = 0
        dataset_versions = 0
        for table_name in engine._list_table_names():
            if not table_name.startswith("mesa_vectors_"):
                continue
            table = engine._db.open_table(table_name)
            stats = table.stats()
            active_fragments += int(stats["fragment_stats"]["num_fragments"])
            dataset_versions += len(table.list_versions())
        return {
            "active_fragments": active_fragments,
            "dataset_versions": dataset_versions,
        }
    except Exception:
        return {"active_fragments": None, "dataset_versions": None}


def _sample(root: Path, operation: int, engine: VectorEngine) -> dict[str, Any]:
    process = psutil.Process()
    memory = process.memory_full_info()
    python_current, python_peak = tracemalloc.get_traced_memory()
    return {
        "operation": operation,
        "rss_bytes": memory.rss,
        "uss_bytes": getattr(memory, "uss", None),
        "pss_bytes": getattr(memory, "pss", None),
        "python_traced_current_bytes": python_current,
        "python_traced_peak_bytes": python_peak,
        "pyarrow_allocated_bytes": pa.total_allocated_bytes(),
        "python_gc_objects": len(gc.get_objects()),
        "threads": process.num_threads(),
        "open_fds": process.num_fds() if hasattr(process, "num_fds") else None,
        **_path_stats(root),
        **_lance_stats(engine),
    }


def _vector(dimension: int, seed: int) -> list[float]:
    return [float((seed + offset) % 23) / 23.0 for offset in range(dimension)]


async def _seed(engine: VectorEngine, count: int, dimension: int) -> None:
    records = [
        {
            "node_id": f"seed-{index}",
            "agent_id": "profile-agent",
            "embedding": _vector(dimension, index),
        }
        for index in range(count)
    ]
    await engine.bulk_upsert(records)


async def run_profile(args: argparse.Namespace) -> dict[str, Any]:
    root = (
        Path(args.existing_uri)
        if args.existing_uri
        else (
            Path(args.uri)
            if args.uri
            else Path(tempfile.mkdtemp(prefix="mesa-vector-"))
        )
    )
    if args.uri and root.exists() and any(root.iterdir()):
        raise ValueError("--uri must point to a new or empty test directory")
    if args.existing_uri and (args.mode != "search" or not root.is_dir()):
        raise ValueError(
            "--existing-uri only supports search against an existing directory"
        )
    root.mkdir(parents=True, exist_ok=True)
    engine = VectorEngine(
        str(root),
        max_workers=2,
        maintenance_enabled=not args.disable_maintenance,
        maintenance_mutation_threshold=args.maintenance_threshold,
        maintenance_min_interval_seconds=args.maintenance_min_interval_seconds,
    )
    upsert_latencies: list[float] = []
    search_latencies: list[float] = []
    samples: list[dict[str, Any]] = []
    maintenance_events: list[dict[str, Any]] = []
    current_operation = 0
    final_record_counts: dict[str, int] = {}
    cleanup_lancedb_loop = _install_pipe_wakeup_workaround()
    tracemalloc.start()
    try:
        await engine.initialize()
        original_optimize = engine._sync_optimize_table

        def profile_optimize(table_name: str) -> None:
            event: dict[str, Any] = {
                "operation": current_operation,
                "table": table_name,
                "before": _sample(root, current_operation, engine),
            }
            started = time.perf_counter()
            original_optimize(table_name)
            event["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
            event["after"] = _sample(root, current_operation, engine)
            maintenance_events.append(event)

        engine._sync_optimize_table = profile_optimize  # type: ignore[method-assign]
        if args.mode in {"search", "update", "mixed"} and not args.existing_uri:
            # Seed with one physical batch.  It is setup, not part of the
            # measured one-row mutation workload, so it must not consume a
            # maintenance threshold or create a baseline compaction event.
            engine._maintenance_enabled = False
            try:
                await _seed(
                    engine, max(args.operations, args.seed_records), args.dimension
                )
            finally:
                engine._maintenance_enabled = not args.disable_maintenance
                engine._mutations_since_maintenance.clear()
                engine._maintenance_last_attempt.clear()
        samples.append(_sample(root, 0, engine))

        for operation in range(1, args.operations + 1):
            current_operation = operation
            start = time.perf_counter()
            if args.mode == "search":
                await engine.search(
                    _vector(args.dimension, operation), agent_id="profile-agent"
                )
                search_latencies.append((time.perf_counter() - start) * 1000)
            elif args.mode == "update":
                await engine.upsert(
                    f"seed-{(operation - 1) % args.operations}",
                    "profile-agent",
                    _vector(args.dimension, operation),
                )
                upsert_latencies.append((time.perf_counter() - start) * 1000)
            elif args.mode == "mixed" and operation % 2 == 0:
                await engine.search(
                    _vector(args.dimension, operation), agent_id="profile-agent"
                )
                search_latencies.append((time.perf_counter() - start) * 1000)
            else:
                await engine.upsert(
                    f"insert-{operation}",
                    "profile-agent",
                    _vector(args.dimension, operation),
                )
                upsert_latencies.append((time.perf_counter() - start) * 1000)

            if operation % args.sample_every == 0 or operation == args.operations:
                samples.append(_sample(root, operation, engine))
            for event in maintenance_events:
                if event["operation"] + 10 == operation:
                    event["ten_operations_later"] = _sample(root, operation, engine)
        final_record_counts = await engine.count_records(active_only=False)
    finally:
        await engine.close()
        tracemalloc.stop()
        cleanup_lancedb_loop()

    return {
        "mode": args.mode,
        "operations": args.operations,
        "dimension": args.dimension,
        "maintenance_enabled": not args.disable_maintenance,
        "maintenance_threshold": args.maintenance_threshold,
        "maintenance_min_interval_seconds": args.maintenance_min_interval_seconds,
        "storage": str(root),
        "samples": samples,
        "maintenance_events": maintenance_events,
        "record_counts": final_record_counts,
        "upsert_latency_ms": {
            "p50": _percentile(upsert_latencies, 0.50),
            "p95": _percentile(upsert_latencies, 0.95),
            "p99": _percentile(upsert_latencies, 0.99),
            "mean": (
                round(statistics.fmean(upsert_latencies), 3)
                if upsert_latencies
                else 0.0
            ),
        },
        "search_latency_ms": {
            "p50": _percentile(search_latencies, 0.50),
            "p95": _percentile(search_latencies, 0.95),
            "p99": _percentile(search_latencies, 0.99),
            "mean": (
                round(statistics.fmean(search_latencies), 3)
                if search_latencies
                else 0.0
            ),
        },
        "vector_metrics": engine.metrics.snapshot(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("search", "insert", "update", "mixed"), required=True
    )
    parser.add_argument("--operations", type=int, default=1000)
    parser.add_argument("--sample-every", type=int, default=100)
    parser.add_argument("--seed-records", type=int, default=100)
    parser.add_argument("--dimension", type=int, default=384)
    parser.add_argument(
        "--uri", help="Fresh test directory; omit for a temporary directory."
    )
    parser.add_argument(
        "--existing-uri",
        help="Existing directory for a fresh-process search/reopen measurement.",
    )
    parser.add_argument("--disable-maintenance", action="store_true")
    parser.add_argument("--maintenance-threshold", type=int, default=128)
    parser.add_argument("--maintenance-min-interval-seconds", type=float, default=0)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.operations < 1 or args.sample_every < 1 or args.dimension < 1:
        raise SystemExit("operations, sample-every, and dimension must be positive")
    if _needs_pipe_wakeup_workaround():
        loop = _PipeWakeupLoop()
        try:
            result = loop.run_until_complete(run_profile(args))
        finally:
            loop.close()
    else:
        result = asyncio.run(run_profile(args))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
