# ADR 0004: Async Background Tasks vs. Celery

## Status
Accepted

## Context
As MESA's node consolidation and dual-LLM routing pathways grew more complex, we needed to ensure high-concurrency background operations would not block the main FastAPI event loop or trigger SQLite WAL `database is locked` exceptions. A traditional enterprise approach would dictate offloading these tasks to a heavy message broker like Celery with Redis or RabbitMQ.

## Decision
We elected to strictly utilize native Python `asyncio` (`FastAPI BackgroundTasks` or dedicated worker coroutines) operating alongside our non-blocking `aiosqlite` Data Access Object (DAO) architecture, eschewing external message brokers. 

To mathematically justify this architectural boundary, we ran an I/O Stress Test simulating 500 concurrent extraction/consolidation tasks routing through the `AdaptiveRouter` and hitting the SQLite WAL database.

## Justification & Benchmark Results
Our benchmark proved that the `asyncio` loop, bounded by concurrency semaphores (e.g., `asyncio.Semaphore(5)` for LLM calls), is highly resilient and deeply performant. Introducing Celery would constitute severe over-engineering, increasing infrastructure overhead and deployment complexity without providing necessary throughput gains for our current tier.

> **BENCHMARK RESULTS**
> ==========================================
> Total Tasks: 500
> Duration: 1.19 seconds
> Throughput: 418.60 tasks/sec
> Event Loop Max Latency: 2.24 ms
> Successful Validations: 500
> Errors/Timeouts: 0
> Error Rate: 0.00%
> ==========================================

## Consequences
- **Positive:** Zero added infrastructure dependencies (No Redis/RabbitMQ required).
- **Positive:** Event loop remains responsive (Max latency < 3ms under extreme load).
- **Positive:** SQLite WAL handles high concurrency natively via async drivers without locking the UI thread.
- **Negative:** If horizontal scaling across multiple disparate servers becomes necessary, a distributed task queue may eventually be required.
