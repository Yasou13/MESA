#!/usr/bin/env python3
# MESA v0.7.0 — Phase 4: Load & Stress Test
# Hot Path Ingestion Endpoint Load Tester
#
# Validates that the asynchronous Hot/Cold Path architecture sustains
# < 50ms latency under heavy concurrency.  Sends realistic Yargıtay
# decision payloads (5-10 KB) against POST /v3/memory/insert and
# collects per-request timing telemetry.
#
# Usage:
#   python -m mesa_evals.load_test                        # defaults
#   python -m mesa_evals.load_test --total 5000 --concurrency 200
#   python -m mesa_evals.load_test --base-url http://10.0.0.5:8000
#
# Exit behaviour:
#   0 — all checks passed
#   1 — p99 latency exceeded 100ms (SEVERE: hot path is blocked)

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sys
import time
import uuid
from dataclasses import dataclass, field

import aiohttp

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "http://localhost:8000"
ENDPOINT_PATH = "/v3/memory/insert"

# Payload size bounds (bytes of generated Turkish legal text)
MIN_PAYLOAD_BYTES = 5 * 1024  # 5 KB
MAX_PAYLOAD_BYTES = 10 * 1024  # 10 KB

# SLA thresholds (milliseconds)
P99_SLA_MS = 100.0  # SEVERE if exceeded
P50_TARGET_MS = 50.0  # informational

logger = logging.getLogger("MESA_LoadTest")

# ---------------------------------------------------------------------------
# Synthetic Yargıtay payload generator
# ---------------------------------------------------------------------------

# Realistic Turkish legal vocabulary fragments used to construct heavy payloads.
# These mimic Yargıtay decision language without requiring external data files.
_YARGITAY_FRAGMENTS: list[str] = [
    "Yargıtay 4. Hukuk Dairesi'nin 2024/1234 E., 2024/5678 K. sayılı kararı "
    "incelendiğinde, davalının haksız fiil sorumluluğu kapsamında tazminat "
    "yükümlülüğünün doğduğu anlaşılmaktadır.",
    "Türk Borçlar Kanunu'nun 49. maddesi gereğince, kusurlu ve hukuka aykırı "
    "bir fiille başkasına zarar veren kişi, bu zararı gidermekle yükümlüdür. "
    "Zarar görenin zararını ve nedensellik bağını ispat yükü altındadır.",
    "Anayasa Mahkemesi'nin 2023/45 sayılı kararında belirtildiği üzere, "
    "temel hak ve özgürlüklerin sınırlandırılması ancak kanunla mümkündür "
    "ve demokratik toplum düzeninin gereklerine aykırı olamaz.",
    "Ceza Muhakemesi Kanunu'nun 100. maddesi kapsamında tutuklama kararı, "
    "kuvvetli suç şüphesinin varlığı ve kaçma ya da delilleri karartma "
    "tehlikesinin bulunması halinde verilebilir.",
    "İdare Mahkemesi, 2577 sayılı İdari Yargılama Usulü Kanunu'nun 27. "
    "maddesi uyarınca yürütmenin durdurulması kararı vermiştir. İdarenin "
    "işleminin açıkça hukuka aykırı olduğu ve telafisi güç zarara yol "
    "açacağı tespit edilmiştir.",
    "Danıştay İdari Dava Daireleri Kurulu'nun emsal niteliğindeki kararına "
    "göre, kamu görevlilerinin disiplin cezalarına itirazlarında silahların "
    "eşitliği ilkesi ve adil yargılanma hakkı gözetilmelidir.",
    "Yargıtay Ceza Genel Kurulu'nun 2024/7-89 E. sayılı kararı, nitelikli "
    "dolandırıcılık suçunun unsurlarını ayrıntılı biçimde ortaya koymuş ve "
    "mağdurun hileli davranışlar sonucu aldatılarak zarara uğratılmasının "
    "suçun maddi unsurunu oluşturduğunu hüküm altına almıştır.",
    "6098 sayılı Türk Borçlar Kanunu'nun 117. maddesi gereğince, muaccel "
    "bir borcun borçlusu, alacaklının ihtarıyla temerrüde düşer. Temerrüt "
    "faizi, akdi faiz oranının yüzde yüz fazlasını aşamaz.",
    "Kişisel Verilerin Korunması Kanunu (KVKK) kapsamında veri sorumlusu, "
    "kişisel verilerin hukuka aykırı olarak işlenmesini önlemek ve verilere "
    "hukuka aykırı olarak erişilmesini engellemek amacıyla uygun güvenlik "
    "düzeyini temin etmeye yönelik gerekli her türlü teknik ve idari "
    "tedbirleri almak zorundadır.",
    "Ticaret Mahkemesi'nce yapılan incelemede, Türk Ticaret Kanunu'nun "
    "18. maddesinde düzenlenen basiretli iş adamı gibi hareket etme "
    "yükümlülüğünün ihlal edildiği ve bunun sözleşmenin feshine yol "
    "açtığı kanaatine varılmıştır.",
]


def _generate_legal_payload() -> dict:
    """Generate a single synthetic Yargıtay decision payload (5-10 KB).

    The payload structure matches MemoryInsertRequest:
        agent_id  — fixed tenant for the load test
        session_id — unique per-request
        content   — concatenated Turkish legal text fragments
        metadata  — simulated case metadata
    """
    target_bytes = random.randint(MIN_PAYLOAD_BYTES, MAX_PAYLOAD_BYTES)
    parts: list[str] = []
    current_bytes = 0

    while current_bytes < target_bytes:
        fragment = random.choice(_YARGITAY_FRAGMENTS)
        parts.append(fragment)
        current_bytes += len(fragment.encode("utf-8"))

    # Inject randomised case identifiers so every payload is unique
    case_year = random.randint(2020, 2025)
    case_num = random.randint(1, 99999)
    docket = f"{case_year}/{case_num} E."

    header = (
        f"T.C. YARGITAY — Karar No: {docket}\n"
        f"Dosya ID: {uuid.uuid4().hex[:12].upper()}\n"
        f"{'=' * 60}\n\n"
    )

    content = header + "\n\n".join(parts)

    return {
        "agent_id": "mesa-load-test-agent",
        "session_id": f"loadtest-{uuid.uuid4().hex[:16]}",
        "content": content,
        "metadata": {
            "source": "load_test",
            "domain": "legal",
            "court": "yargitay",
            "docket": docket,
            "case_year": str(case_year),
            "generator": "mesa_evals.load_test",
        },
    }


# ---------------------------------------------------------------------------
# Metrics collection
# ---------------------------------------------------------------------------


@dataclass
class LoadTestResult:
    """Aggregated metrics from a single load-test run."""

    total_requests: int = 0
    completed_requests: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    status_codes: dict[int, int] = field(default_factory=dict)
    errors: int = 0
    wall_clock_seconds: float = 0.0

    # -- Derived statistics --------------------------------------------------

    @property
    def avg_latency_ms(self) -> float:
        return (
            sum(self.latencies_ms) / len(self.latencies_ms)
            if self.latencies_ms
            else 0.0
        )

    @property
    def min_latency_ms(self) -> float:
        return min(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def max_latency_ms(self) -> float:
        return max(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def rps(self) -> float:
        return (
            self.completed_requests / self.wall_clock_seconds
            if self.wall_clock_seconds > 0
            else 0.0
        )

    def percentile(self, p: float) -> float:
        """Calculate the p-th percentile latency (0-100 scale)."""
        if not self.latencies_ms:
            return 0.0
        sorted_lat = sorted(self.latencies_ms)
        idx = int(len(sorted_lat) * p / 100.0)
        idx = min(idx, len(sorted_lat) - 1)
        return sorted_lat[idx]

    @property
    def p50_ms(self) -> float:
        return self.percentile(50)

    @property
    def p95_ms(self) -> float:
        return self.percentile(95)

    @property
    def p99_ms(self) -> float:
        return self.percentile(99)


# ---------------------------------------------------------------------------
# Core load test engine
# ---------------------------------------------------------------------------


async def _send_single_request(
    session: aiohttp.ClientSession,
    url: str,
    semaphore: asyncio.Semaphore,
    result: LoadTestResult,
    request_id: int,
    headers: dict[str, str] | None = None,
) -> None:
    """Send a single POST request and record its latency + status code."""
    payload = _generate_legal_payload()

    async with semaphore:
        t_start = time.monotonic()
        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                latency_ms = (time.monotonic() - t_start) * 1000.0
                status = resp.status

                result.latencies_ms.append(latency_ms)
                result.status_codes[status] = result.status_codes.get(status, 0) + 1
                result.completed_requests += 1

                if request_id % 200 == 0 or latency_ms > P99_SLA_MS:
                    logger.debug(
                        "REQ #%05d | status=%d latency=%.1fms",
                        request_id,
                        status,
                        latency_ms,
                    )
        except aiohttp.ClientError as exc:
            latency_ms = (time.monotonic() - t_start) * 1000.0
            result.errors += 1
            result.completed_requests += 1
            logger.warning(
                "REQ #%05d | CONNECTION_ERROR after %.1fms: %s",
                request_id,
                latency_ms,
                exc,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result.errors += 1
            result.completed_requests += 1
            logger.error(
                "REQ #%05d | UNEXPECTED_ERROR: %s",
                request_id,
                exc,
            )


async def run_load_test(
    total_requests: int = 1000,
    concurrency_limit: int = 100,
    base_url: str = DEFAULT_BASE_URL,
    api_key: str | None = None,
) -> LoadTestResult:
    """Execute the load test against the MESA Hot Path endpoint.

    Fires ``total_requests`` concurrent POST requests against
    ``/v3/memory/insert``, throttled by ``concurrency_limit`` via an
    asyncio.Semaphore.

    Args:
        total_requests: Total number of requests to send.
        concurrency_limit: Maximum simultaneous in-flight requests.
        base_url: MESA API base URL (e.g. http://localhost:8000).

    Returns:
        LoadTestResult with all collected metrics.
    """
    url = f"{base_url.rstrip('/')}{ENDPOINT_PATH}"
    semaphore = asyncio.Semaphore(concurrency_limit)
    result = LoadTestResult(total_requests=total_requests)

    # Connection pool sized to concurrency limit for maximum throughput
    connector = aiohttp.TCPConnector(
        limit=concurrency_limit,
        limit_per_host=concurrency_limit,
        enable_cleanup_closed=True,
    )
    timeout = aiohttp.ClientTimeout(total=30, connect=10)

    # Build optional auth headers
    req_headers: dict[str, str] | None = None
    if api_key:
        req_headers = {"X-API-Key": api_key}

    print(f"\n{'=' * 70}")
    print("  MESA v0.7.0 — Hot Path Load Test")
    print(f"{'=' * 70}")
    print(f"  Target:       {url}")
    print(
        f"  Auth:         {'X-API-Key ***' + api_key[-4:] if api_key else 'DISABLED'}"
    )
    print(f"  Requests:     {total_requests:,}")
    print(f"  Concurrency:  {concurrency_limit}")
    print(
        f"  Payload Size: {MIN_PAYLOAD_BYTES // 1024}-{MAX_PAYLOAD_BYTES // 1024} KB (Yargıtay decisions)"
    )
    print(f"  SLA (p99):    < {P99_SLA_MS:.0f}ms")
    print(f"{'=' * 70}\n")

    t_wall_start = time.monotonic()

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        tasks = [
            _send_single_request(
                session, url, semaphore, result, i, headers=req_headers
            )
            for i in range(total_requests)
        ]
        await asyncio.gather(*tasks)

    result.wall_clock_seconds = time.monotonic() - t_wall_start

    return result


# ---------------------------------------------------------------------------
# Report renderer
# ---------------------------------------------------------------------------


def _render_report(result: LoadTestResult) -> bool:
    """Print a formatted report. Returns True if SLA passed, False otherwise."""

    sla_passed = result.p99_ms <= P99_SLA_MS

    print(f"\n{'=' * 70}")
    print("  LOAD TEST RESULTS")
    print(f"{'=' * 70}")

    # -- Throughput ----------------------------------------------------------
    print("\n  ┌─ Throughput ──────────────────────────────────────────────┐")
    print(
        f"  │  Total Requests:     {result.total_requests:>10,}                      │"
    )
    print(
        f"  │  Completed:          {result.completed_requests:>10,}                      │"
    )
    print(f"  │  Errors:             {result.errors:>10,}                      │")
    print(
        f"  │  Wall Clock:         {result.wall_clock_seconds:>10.2f}s                     │"
    )
    print(f"  │  Requests/sec (RPS): {result.rps:>10.1f}                      │")
    print("  └─────────────────────────────────────────────────────────────┘")

    # -- Latency distribution ------------------------------------------------
    print("\n  ┌─ Latency Distribution (ms) ─────────────────────────────┐")
    print(
        f"  │  Min:                {result.min_latency_ms:>10.2f}                      │"
    )
    print(
        f"  │  Avg:                {result.avg_latency_ms:>10.2f}                      │"
    )
    print(f"  │  p50 (median):       {result.p50_ms:>10.2f}                      │")
    print(f"  │  p95:                {result.p95_ms:>10.2f}                      │")
    print(f"  │  p99:                {result.p99_ms:>10.2f}                      │")
    print(
        f"  │  Max:                {result.max_latency_ms:>10.2f}                      │"
    )
    print("  └─────────────────────────────────────────────────────────────┘")

    # -- Status code distribution --------------------------------------------
    print("\n  ┌─ Status Code Distribution ──────────────────────────────┐")
    for code in sorted(result.status_codes.keys()):
        count = result.status_codes[code]
        pct = (
            count / result.completed_requests * 100 if result.completed_requests else 0
        )
        label = {202: "Accepted", 429: "Too Many Requests", 500: "Internal Error"}.get(
            code, "Other"
        )
        bar = "█" * int(pct / 2)
        print(f"  │  {code} {label:<20s} {count:>7,} ({pct:>5.1f}%) {bar:<20s}│")
    if result.errors > 0:
        err_pct = (
            result.errors / result.completed_requests * 100
            if result.completed_requests
            else 0
        )
        print(
            f"  │  --- Connection Errors  {result.errors:>7,} ({err_pct:>5.1f}%)                    │"
        )
    print("  └─────────────────────────────────────────────────────────────┘")

    # -- SLA verdict ---------------------------------------------------------
    print()
    if sla_passed:
        print(
            f"  ✅ SLA PASSED — p99 latency {result.p99_ms:.2f}ms < {P99_SLA_MS:.0f}ms threshold"
        )
        if result.p50_ms <= P50_TARGET_MS:
            print(
                f"  ✅ HOT PATH TARGET MET — p50 {result.p50_ms:.2f}ms < {P50_TARGET_MS:.0f}ms"
            )
        else:
            print(
                f"  ⚠️  HOT PATH PRESSURE — p50 {result.p50_ms:.2f}ms > {P50_TARGET_MS:.0f}ms target"
            )
    else:
        print("  ╔══════════════════════════════════════════════════════════╗")
        print("  ║  🚨 SEVERE: HOT PATH IS BLOCKED                        ║")
        print(
            f"  ║  p99 latency {result.p99_ms:.2f}ms EXCEEDS {P99_SLA_MS:.0f}ms SLA          ║"
        )
        print("  ║                                                          ║")
        print("  ║  Root Cause Analysis:                                    ║")
        print("  ║  - Cold-path processing may be leaking into hot path     ║")
        print("  ║  - WAL contention under concurrent INSERT pressure       ║")
        print("  ║  - Background task pool exhaustion                       ║")
        print("  ╚══════════════════════════════════════════════════════════╝")

    print(f"\n{'=' * 70}\n")

    return sla_passed


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mesa_load_test",
        description="MESA v0.7.0 — Hot Path Ingestion Load & Stress Tester",
    )
    parser.add_argument(
        "--total",
        "-n",
        type=int,
        default=1000,
        help="Total number of requests to send (default: 1000)",
    )
    parser.add_argument(
        "--concurrency",
        "-c",
        type=int,
        default=100,
        help="Maximum concurrent in-flight requests (default: 100)",
    )
    parser.add_argument(
        "--base-url",
        "-u",
        type=str,
        default=DEFAULT_BASE_URL,
        help=f"MESA API base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--api-key",
        "-k",
        type=str,
        default=None,
        help="X-API-Key header value (omit for --no-auth dev server)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable DEBUG logging for per-request telemetry",
    )
    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Execute
    result = asyncio.run(
        run_load_test(
            total_requests=args.total,
            concurrency_limit=args.concurrency,
            base_url=args.base_url,
            api_key=args.api_key,
        )
    )

    # Report
    sla_passed = _render_report(result)

    # Exit code: 1 if p99 SLA breached
    sys.exit(0 if sla_passed else 1)


if __name__ == "__main__":
    main()
