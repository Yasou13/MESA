"""MESA v0.4.1 — Phase 4B: Soak Test — Memory Leak & Queue Stability Monitor.

Sends a constant, moderate load to ``POST /v3/memory/insert`` over a
configurable duration (default: 12 hours) and monitors system health via
periodic telemetry snapshots.

Architecture:
    ┌──────────────┐    20 req/s    ┌──────────────────┐
    │  Load Driver  │──────────────▶│ /v3/memory/insert │
    └──────────────┘                └──────────────────┘
           │
           │  every 60s
           ▼
    ┌──────────────┐
    │  Telemetry    │──▶ /health + /v3/memory/status polling
    │  Collector    │──▶ queue_depth, success/fail ratio, latency
    └──────────────┘

Metrics tracked:
    - Cumulative success / failure counts and ratio
    - Current ``raw_logs`` queue depth (status == 'queued')
    - P50 / P99 insert latency
    - Health endpoint status
    - Process RSS memory (via /proc/self/status if available)

Output:
    - Structured JSON-lines log to ``soak_test_YYYYMMDD_HHMMSS.jsonl``
    - Human-readable console summary via standard logging

Usage::

    python -m mesa_evals.soak_test
    python -m mesa_evals.soak_test --duration 3600 --rps 10
    python -m mesa_evals.soak_test --base-url http://prod:8000 --api-key SECRET
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import resource
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger("MESA_SoakTest")

# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_DURATION = 43_200  # 12 hours
DEFAULT_RPS = 20  # requests per second
DEFAULT_TELEMETRY_INTERVAL = 60  # seconds
DEFAULT_CONCURRENCY = 30
DEFAULT_AGENT_ID = "soak-test-agent"
DEFAULT_SESSION_ID = "soak-test-session"
QUEUE_SAMPLE_SIZE = 50  # number of recent log_ids to sample for queue depth


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SoakMetrics:
    """Mutable accumulator for soak test telemetry."""

    total_requests: int = 0
    success_count: int = 0
    failure_count: int = 0
    http_errors: dict[int, int] = field(default_factory=dict)
    latencies_ms: list[float] = field(default_factory=list)
    recent_log_ids: list[int] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def record(
        self, *, success: bool, latency_ms: float, log_id: int | None, status_code: int
    ) -> None:
        async with self._lock:
            self.total_requests += 1
            if success:
                self.success_count += 1
                if log_id is not None:
                    self.recent_log_ids.append(log_id)
                    # Keep a bounded window for queue depth sampling
                    if len(self.recent_log_ids) > QUEUE_SAMPLE_SIZE * 2:
                        self.recent_log_ids = self.recent_log_ids[-QUEUE_SAMPLE_SIZE:]
            else:
                self.failure_count += 1
                self.http_errors[status_code] = self.http_errors.get(status_code, 0) + 1
            self.latencies_ms.append(latency_ms)

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            sorted_lat = sorted(self.latencies_ms) if self.latencies_ms else [0.0]
            n = len(sorted_lat)
            return {
                "total_requests": self.total_requests,
                "success_count": self.success_count,
                "failure_count": self.failure_count,
                "success_ratio": (
                    round(self.success_count / self.total_requests, 6)
                    if self.total_requests > 0
                    else 0.0
                ),
                "http_errors": dict(self.http_errors),
                "latency_p50_ms": round(sorted_lat[n // 2], 2),
                "latency_p99_ms": round(sorted_lat[int(n * 0.99)], 2),
                "latency_mean_ms": round(sum(sorted_lat) / n, 2) if n else 0.0,
                "recent_log_id_count": len(self.recent_log_ids),
            }


# ---------------------------------------------------------------------------
# Synthetic payload generator
# ---------------------------------------------------------------------------


def _make_payload(seq: int) -> dict[str, Any]:
    """Generate a synthetic insert payload for soak testing.

    Each payload is unique to prevent dedup from short-circuiting
    the cold path.
    """
    ts = datetime.now(timezone.utc).isoformat()
    return {
        "agent_id": DEFAULT_AGENT_ID,
        "session_id": DEFAULT_SESSION_ID,
        "content": (
            f"Soak test entry #{seq} — {ts}. "
            f"Türk Borçlar Kanunu m.49 kapsamında haksız fiil "
            f"sorumluluğu değerlendirmesi. Yargıtay kararları "
            f"doğrultusunda tazminat hesaplaması yapılmıştır."
        ),
        "metadata": {
            "soak_seq": seq,
            "timestamp": ts,
            "is_soak_test": "true",
        },
    }


# ---------------------------------------------------------------------------
# Load driver
# ---------------------------------------------------------------------------


async def _send_one(
    session: aiohttp.ClientSession,
    *,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    metrics: SoakMetrics,
    semaphore: asyncio.Semaphore,
) -> None:
    """Send a single insert request and record the result."""
    async with semaphore:
        t0 = time.monotonic()
        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                latency = (time.monotonic() - t0) * 1000
                body = await resp.json()
                log_id = body.get("log_id") if resp.status == 202 else None
                await metrics.record(
                    success=(resp.status == 202),
                    latency_ms=latency,
                    log_id=log_id,
                    status_code=resp.status,
                )
        except Exception as exc:
            latency = (time.monotonic() - t0) * 1000
            logger.debug(
                "INSERT_ERROR | seq=%s error=%s",
                payload.get("metadata", {}).get("soak_seq"),
                exc,
            )
            await metrics.record(
                success=False,
                latency_ms=latency,
                log_id=None,
                status_code=0,
            )


async def load_driver(
    session: aiohttp.ClientSession,
    *,
    base_url: str,
    api_key: str,
    rps: int,
    duration: float,
    concurrency: int,
    metrics: SoakMetrics,
    stop_event: asyncio.Event,
) -> None:
    """Drive constant load at the configured RPS for the duration."""
    url = f"{base_url}/v3/memory/insert"
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    semaphore = asyncio.Semaphore(concurrency)

    interval = 1.0 / rps  # seconds between requests
    seq = 0
    t_start = time.monotonic()

    logger.info(
        "LOAD_DRIVER | Starting: %d req/s for %ds (concurrency=%d)",
        rps,
        int(duration),
        concurrency,
    )

    while not stop_event.is_set() and (time.monotonic() - t_start) < duration:
        payload = _make_payload(seq)
        asyncio.create_task(
            _send_one(
                session,
                url=url,
                headers=headers,
                payload=payload,
                metrics=metrics,
                semaphore=semaphore,
            )
        )
        seq += 1
        await asyncio.sleep(interval)

    logger.info("LOAD_DRIVER | Stopped after %d requests", seq)


# ---------------------------------------------------------------------------
# Telemetry collector
# ---------------------------------------------------------------------------


def _get_rss_mb() -> float:
    """Return current process RSS in MB (Linux/macOS)."""
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        # maxrss is in KB on Linux, bytes on macOS
        if sys.platform == "darwin":
            return usage.ru_maxrss / (1024 * 1024)
        return usage.ru_maxrss / 1024
    except Exception:
        return -1.0


async def _poll_queue_depth(
    session: aiohttp.ClientSession,
    *,
    base_url: str,
    api_key: str,
    log_ids: list[int],
) -> dict[str, int]:
    """Sample recent log_ids to estimate queue depth.

    Queries ``GET /v3/memory/status/{log_id}`` for a sample of recent
    log_ids and counts how many are still in ``queued`` state.
    """
    headers = {"X-API-Key": api_key}
    # Sample the most recent log_ids
    sample = log_ids[-QUEUE_SAMPLE_SIZE:] if log_ids else []
    status_counts: dict[str, int] = {}

    for log_id in sample:
        try:
            async with session.get(
                f"{base_url}/v3/memory/status/{log_id}", headers=headers
            ) as resp:
                if resp.status == 200:
                    body = await resp.json()
                    status = body.get("status", "unknown")
                    status_counts[status] = status_counts.get(status, 0) + 1
                elif resp.status == 404:
                    status_counts["not_found"] = status_counts.get("not_found", 0) + 1
        except Exception:
            status_counts["error"] = status_counts.get("error", 0) + 1

    return status_counts


async def telemetry_collector(
    session: aiohttp.ClientSession,
    *,
    base_url: str,
    api_key: str,
    metrics: SoakMetrics,
    interval: float,
    log_file: Path,
    stop_event: asyncio.Event,
) -> None:
    """Periodically collect and log system telemetry."""
    headers = {"X-API-Key": api_key}
    t_start = time.monotonic()
    tick = 0

    logger.info("TELEMETRY | Starting collector (interval=%ds)", int(interval))

    while not stop_event.is_set():
        await asyncio.sleep(interval)
        tick += 1

        elapsed = time.monotonic() - t_start
        snap = await metrics.snapshot()

        # Poll health endpoint
        health_status = "unknown"
        try:
            async with session.get(f"{base_url}/health", headers=headers) as resp:
                if resp.status == 200:
                    health_body = await resp.json()
                    health_status = health_body.get("status", "unknown")
                else:
                    health_status = f"http_{resp.status}"
        except Exception as exc:
            health_status = f"error:{exc.__class__.__name__}"

        # Poll queue depth from recent log_ids
        async with metrics._lock:
            sample_ids = list(metrics.recent_log_ids[-QUEUE_SAMPLE_SIZE:])
        queue_status = await _poll_queue_depth(
            session, base_url=base_url, api_key=api_key, log_ids=sample_ids
        )

        rss_mb = _get_rss_mb()

        telemetry_record = {
            "tick": tick,
            "elapsed_s": round(elapsed, 1),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "health": health_status,
            "rss_mb": round(rss_mb, 1),
            "queue_depth": queue_status,
            "queued_count": queue_status.get("queued", 0),
            "processed_count": queue_status.get("processed", 0),
            **snap,
        }

        # Write JSON-line to log file
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(telemetry_record, ensure_ascii=False) + "\n")

        # Human-readable console log
        logger.info(
            "TELEMETRY [%04d] | elapsed=%ds | reqs=%d | ok=%d | fail=%d | "
            "ratio=%.4f | p50=%.1fms | p99=%.1fms | queue=%d | "
            "health=%s | rss=%.1fMB",
            tick,
            int(elapsed),
            snap["total_requests"],
            snap["success_count"],
            snap["failure_count"],
            snap["success_ratio"],
            snap["latency_p50_ms"],
            snap["latency_p99_ms"],
            queue_status.get("queued", 0),
            health_status,
            rss_mb,
        )

    logger.info("TELEMETRY | Collector stopped after %d ticks", tick)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


async def run_soak(
    *,
    base_url: str,
    api_key: str,
    duration: float,
    rps: int,
    concurrency: int,
    telemetry_interval: float,
    log_file: Path,
) -> dict[str, Any]:
    """Execute the full soak test pipeline."""
    metrics = SoakMetrics()
    stop_event = asyncio.Event()

    connector = aiohttp.TCPConnector(
        limit=concurrency,
        enable_cleanup_closed=True,
    )
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        # Pre-flight health check
        logger.info("PRE-FLIGHT | Checking server health at %s...", base_url)
        try:
            async with session.get(
                f"{base_url}/health", headers={"X-API-Key": api_key}
            ) as resp:
                if resp.status != 200:
                    logger.error(
                        "PRE-FLIGHT FAILED | Health check returned %d", resp.status
                    )
                    return {"status": "pre_flight_failed", "http_status": resp.status}
                health = await resp.json()
                logger.info("PRE-FLIGHT | Server healthy: %s", health.get("status"))
        except Exception as exc:
            logger.error("PRE-FLIGHT FAILED | Cannot reach server: %s", exc)
            return {"status": "pre_flight_failed", "error": str(exc)}

        # Launch concurrent tasks
        load_task = asyncio.create_task(
            load_driver(
                session,
                base_url=base_url,
                api_key=api_key,
                rps=rps,
                duration=duration,
                concurrency=concurrency,
                metrics=metrics,
                stop_event=stop_event,
            )
        )
        telemetry_task = asyncio.create_task(
            telemetry_collector(
                session,
                base_url=base_url,
                api_key=api_key,
                metrics=metrics,
                interval=telemetry_interval,
                log_file=log_file,
                stop_event=stop_event,
            )
        )

        try:
            # Wait for the load driver to finish (it runs for `duration` seconds)
            await load_task
        except asyncio.CancelledError:
            logger.warning("SOAK | Load driver cancelled")
        except KeyboardInterrupt:
            logger.warning("SOAK | Interrupted by user")
        finally:
            stop_event.set()
            # Give telemetry one last tick to flush
            await asyncio.sleep(2)
            telemetry_task.cancel()
            try:
                await telemetry_task
            except asyncio.CancelledError:
                pass

    # Final report
    final_snap = await metrics.snapshot()
    final_snap["log_file"] = str(log_file)
    final_snap["duration_requested_s"] = duration
    final_snap["rps_target"] = rps

    return final_snap


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entrypoint for the MESA Soak Test."""
    parser = argparse.ArgumentParser(
        description="MESA v0.4.1 — Phase 4B: Soak Test (Memory Leak & Queue Stability)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=DEFAULT_BASE_URL,
        help=f"MESA API base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=os.environ.get("MESA_API_KEY", ""),
        help="API key (default: $MESA_API_KEY)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=DEFAULT_DURATION,
        help=f"Test duration in seconds (default: {DEFAULT_DURATION} = 12 hours)",
    )
    parser.add_argument(
        "--rps",
        type=int,
        default=DEFAULT_RPS,
        help=f"Target requests per second (default: {DEFAULT_RPS})",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Max concurrent HTTP connections (default: {DEFAULT_CONCURRENCY})",
    )
    parser.add_argument(
        "--telemetry-interval",
        type=float,
        default=DEFAULT_TELEMETRY_INTERVAL,
        help=f"Telemetry collection interval in seconds (default: {DEFAULT_TELEMETRY_INTERVAL})",
    )
    parser.add_argument(
        "--log-dir",
        type=str,
        default=".",
        help="Directory for telemetry log files (default: current directory)",
    )
    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.api_key:
        logger.error("MESA_API_KEY not set. Pass --api-key or set the env variable.")
        sys.exit(1)

    # Generate timestamped log filename
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = Path(args.log_dir) / f"soak_test_{ts}.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "SOAK_CONFIG | duration=%ds rps=%d concurrency=%d telemetry=%ds log=%s",
        args.duration,
        args.rps,
        args.concurrency,
        int(args.telemetry_interval),
        log_file,
    )

    try:
        final_report = asyncio.run(
            run_soak(
                base_url=args.base_url,
                api_key=args.api_key,
                duration=args.duration,
                rps=args.rps,
                concurrency=args.concurrency,
                telemetry_interval=args.telemetry_interval,
                log_file=log_file,
            )
        )
    except KeyboardInterrupt:
        logger.warning("SOAK | Aborted by user (Ctrl+C)")
        sys.exit(130)

    # Print final summary
    print("\n" + "=" * 72)
    print("  MESA v0.4.1 SOAK TEST — FINAL REPORT")
    print("=" * 72)
    print(json.dumps(final_report, ensure_ascii=False, indent=2))
    print("=" * 72)

    # Write final report as last entry in log file
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(
            json.dumps({"_type": "final_report", **final_report}, ensure_ascii=False)
            + "\n"
        )

    logger.info("SOAK | Telemetry log: %s", log_file)

    # Exit code: non-zero if failure ratio > 5%
    if final_report.get("total_requests", 0) > 0:
        fail_ratio = final_report["failure_count"] / final_report["total_requests"]
        if fail_ratio > 0.05:
            logger.error(
                "SOAK_FAIL | Failure ratio %.2f%% exceeds 5%% threshold",
                fail_ratio * 100,
            )
            sys.exit(1)

    logger.info("SOAK_PASS | Test completed successfully")


if __name__ == "__main__":
    main()
