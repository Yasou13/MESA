"""D112 concurrency test: 8 parallel clients (4 uploading, 4 searching/asking) + writer lock."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest

from mesa_storage.writer_lock import StorageWriterLock
from tests.fake_openai_server import FakeOpenAIServer, chat_completion_payload

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _probe_rival_writer_lock(storage_root: Path) -> bool:
    """Return True if a separate process was properly rejected by StorageWriterLock."""
    code = "\n".join(
        [
            "from pathlib import Path",
            "import sys",
            "from mesa_storage.writer_lock import StorageWriterLock, StorageWriterLockError",
            "try:",
            "    lock = StorageWriterLock.acquire(Path(sys.argv[1]), owner='rival-writer')",
            "    lock.release()",
            "    sys.exit(99)",
            "except StorageWriterLockError:",
            "    sys.exit(0)",
        ]
    )
    res = subprocess.run(
        [sys.executable, "-c", code, str(storage_root)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert res.returncode in (0, 99), f"probe script error ({res.returncode}): {res.stderr}"
    return res.returncode == 0


def _make_document_text(worker_id: int, seq: int) -> str:
    """Build a deterministic multi-paragraph document that spans multiple chunks."""
    paragraphs = [
        (
            f"Concurrency document worker {worker_id} sequence {seq}. "
            f"Madde {seq + 1}: Tebligat usulü ve süreler hakkında genel hüküm. "
            + ("Kira sözleşmesi ve tahliye bildirimi esasları ayrıntılı olarak düzenlenir. " * 22)
        )
        for _ in range(3)
    ]
    return "\n\n".join(paragraphs)


def _fake_llm_handler(
    path: str, _headers: dict[str, str], body: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    assert path == "/v1/chat/completions"
    payload = json.dumps(
        {
            "answer": "Tebligat ve kira esasları kaynak metinde yer almaktadır.",
            "citations": [1],
            "not_found": False,
        }
    )
    return 200, chat_completion_payload(payload, model=str(body.get("model") or "fake-llm"))


def _stop_server(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


@pytest.mark.slow
def test_eight_parallel_clients_no_5xx_no_chunk_loss_and_single_writer_lock(
    tmp_path: Path,
) -> None:
    """D112: 4 uploaders + 4 search/ask readers + rival lock probes on one store.

    Default duration is 15 s so the full gate stays bounded; set
    ``MESA_CONCURRENCY_DURATION_SECONDS=600`` for the 10-minute certification run.
    """
    duration_s = float(os.environ.get("MESA_CONCURRENCY_DURATION_SECONDS", "15"))
    home = tmp_path / "mesa_home"
    storage_root = home / "data"
    port = _pick_free_port()
    base_url = f"http://127.0.0.1:{port}"
    log_path = tmp_path / "server.log"

    with FakeOpenAIServer(_fake_llm_handler) as fake_llm:
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("MESA_", "LLM_"))
        }
        env.update(
            {
                "MESA_HOME": str(home),
                "MESA_STORAGE_ROOT": str(storage_root),
                "MESA_PORT": str(port),
                "MESA_MODEL_ENABLED": "false",
                "MESA_EMBEDDING_ENABLED": "false",
                "MESA_EXTRACTION_ENABLED": "false",
                "MESA_EXTERNAL_PROVIDER_ENABLED": "false",
                "MESA_LLM_BASE_URL": fake_llm.base_url,
                "MESA_LLM_MODEL": "fake-concurrency-llm",
                "MESA_LLM_API_KEY": "local",
            }
        )

        with log_path.open("wb") as log_fh:
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "mesa_mcp.cli",
                    "start",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--home",
                    str(home),
                    "--storage-root",
                    str(storage_root),
                ],
                cwd=str(REPO_ROOT),
                env=env,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
            )

        uploaded: dict[str, dict[str, Any]] = {}
        uploaded_lock = threading.Lock()
        http_5xx: list[dict[str, Any]] = []
        unexpected_errors: list[str] = []
        errors_lock = threading.Lock()
        lock_probe_results: list[bool] = []
        stats = {"uploads": 0, "searches": 0, "asks": 0}
        stats_lock = threading.Lock()

        try:
            deadline = time.monotonic() + 60.0
            ready = False
            with httpx.Client(timeout=5.0) as probe_client:
                while time.monotonic() < deadline:
                    if server.poll() is not None:
                        raise AssertionError(
                            f"Server exited early ({server.returncode}):\n"
                            + log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
                        )
                    try:
                        resp = probe_client.get(f"{base_url}/health/init")
                        if resp.status_code == 200:
                            ready = True
                            break
                    except httpx.HTTPError:
                        time.sleep(0.25)
            assert ready, (
                "Timed out waiting for /health/init:\n"
                + log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
            )

            creds = json.loads((home / "credentials.json").read_text(encoding="utf-8"))
            headers = {"X-API-Key": creds["api_key"]}
            scope = {
                "tenant_id": creds["tenant_id"],
                "workspace_id": creds["workspace_id"],
                "dataset_id": creds["dataset_id"],
            }
            search_scope = {
                "tenant_id": creds["tenant_id"],
                "workspace_id": creds["workspace_id"],
                "dataset_ids": [creds["dataset_id"]],
            }

            # Bootstrap catalog scope (tenant/workspace/dataset) then seed 2 documents.
            with httpx.Client(base_url=base_url, headers=headers, timeout=60.0) as seed_client:
                ws = seed_client.post(
                    "/v4/catalog/workspaces",
                    json={
                        "tenant_id": creds["tenant_id"],
                        "workspace_id": creds["workspace_id"],
                        "name": "Default Workspace",
                    },
                )
                assert ws.status_code in (200, 201, 409), ws.text
                ds = seed_client.post(
                    "/v4/catalog/datasets",
                    json={
                        "tenant_id": creds["tenant_id"],
                        "workspace_id": creds["workspace_id"],
                        "dataset_id": creds["dataset_id"],
                        "name": "Default Dataset",
                    },
                )
                assert ds.status_code in (200, 201, 409), ds.text
                for idx in range(2):
                    doc_id = f"seed-doc-{idx}"
                    content = _make_document_text(99, idx)
                    r = seed_client.post(
                        "/v4/documents",
                        json={
                            **scope,
                            "document_id": doc_id,
                            "title": f"Seed Document {idx}",
                            "source_ref": f"concurrency://seed/{doc_id}",
                            "content": content,
                        },
                    )
                    assert r.status_code == 202, r.text
                    body = r.json()
                    uploaded[doc_id] = {
                        "content": content,
                        "revision_id": body["revision_id"],
                        "chunk_count": int(body["chunk_count"]),
                    }
                seed_deadline = time.monotonic() + 60.0
                while time.monotonic() < seed_deadline:
                    all_active = True
                    for doc_id in list(uploaded):
                        st = seed_client.get(f"/v4/documents/{doc_id}", params=scope)
                        assert st.status_code == 200
                        revs = st.json().get("revisions") or []
                        if not revs or revs[0]["stage"] != "active":
                            all_active = False
                            break
                    if all_active:
                        break
                    time.sleep(0.2)

            stop_at = time.monotonic() + duration_s
            stop_event = threading.Event()

            def _record_5xx(kind: str, status: int, detail: str) -> None:
                with errors_lock:
                    http_5xx.append({"kind": kind, "status": status, "detail": detail[:300]})
                    if len(http_5xx) >= 3:
                        stop_event.set()

            def _upload_worker(worker_id: int) -> None:
                seq = 0
                with httpx.Client(base_url=base_url, headers=headers, timeout=60.0) as client:
                    while time.monotonic() < stop_at and not stop_event.is_set():
                        doc_id = f"w{worker_id}-doc-{seq:05d}"
                        content = _make_document_text(worker_id, seq)
                        try:
                            resp = client.post(
                                "/v4/documents",
                                json={
                                    **scope,
                                    "document_id": doc_id,
                                    "title": f"Worker {worker_id} Doc {seq}",
                                    "source_ref": f"concurrency://w{worker_id}/{doc_id}",
                                    "content": content,
                                },
                            )
                        except Exception as exc:
                            with errors_lock:
                                unexpected_errors.append(f"upload w{worker_id} #{seq}: {exc!r}")
                            continue
                        if resp.status_code >= 500:
                            _record_5xx("upload", resp.status_code, resp.text)
                        elif resp.status_code != 202:
                            with errors_lock:
                                unexpected_errors.append(
                                    f"upload w{worker_id} #{seq}: HTTP {resp.status_code} {resp.text[:200]}"
                                )
                        else:
                            body = resp.json()
                            with uploaded_lock:
                                uploaded[doc_id] = {
                                    "content": content,
                                    "revision_id": body["revision_id"],
                                    "chunk_count": int(body["chunk_count"]),
                                }
                            with stats_lock:
                                stats["uploads"] += 1
                        seq += 1

            def _reader_worker(worker_id: int) -> None:
                step = 0
                queries = (
                    "Tebligat usulü ve süreler",
                    "Kira sözleşmesi tahliye bildirimi",
                    "Madde 1 genel hüküm",
                )
                with httpx.Client(base_url=base_url, headers=headers, timeout=60.0) as client:
                    while time.monotonic() < stop_at and not stop_event.is_set():
                        query = queries[(worker_id + step) % len(queries)]
                        if step % 2 == 0:
                            try:
                                resp = client.post(
                                    "/v4/memory/search",
                                    json={**search_scope, "query": query, "limit": 5},
                                )
                            except Exception as exc:
                                with errors_lock:
                                    unexpected_errors.append(f"search r{worker_id} #{step}: {exc!r}")
                                step += 1
                                continue
                            if resp.status_code >= 500:
                                _record_5xx("search", resp.status_code, resp.text)
                            elif resp.status_code != 200:
                                with errors_lock:
                                    unexpected_errors.append(
                                        f"search r{worker_id} #{step}: HTTP {resp.status_code} {resp.text[:200]}"
                                    )
                            else:
                                with stats_lock:
                                    stats["searches"] += 1
                        else:
                            try:
                                resp = client.post(
                                    "/v4/ask",
                                    json={**search_scope, "question": query, "limit": 4},
                                )
                            except Exception as exc:
                                with errors_lock:
                                    unexpected_errors.append(f"ask r{worker_id} #{step}: {exc!r}")
                                step += 1
                                continue
                            if resp.status_code >= 500:
                                _record_5xx("ask", resp.status_code, resp.text)
                            elif resp.status_code != 200:
                                with errors_lock:
                                    unexpected_errors.append(
                                        f"ask r{worker_id} #{step}: HTTP {resp.status_code} {resp.text[:200]}"
                                    )
                            else:
                                body = resp.json()
                                assert "answer" in body and "citations" in body
                                with stats_lock:
                                    stats["asks"] += 1
                        step += 1

            def _lock_probe_loop() -> None:
                while time.monotonic() < stop_at and not stop_event.is_set():
                    lock_probe_results.append(_probe_rival_writer_lock(storage_root))
                    stop_event.wait(min(2.0, max(0.5, duration_s / 5.0)))

            probe_thread = threading.Thread(target=_lock_probe_loop, daemon=True)
            probe_thread.start()
            try:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    futures = [pool.submit(_upload_worker, i) for i in range(4)]
                    futures.extend(pool.submit(_reader_worker, i) for i in range(4))
                    for fut in futures:
                        fut.result()
            finally:
                stop_event.set()
                probe_thread.join(timeout=20.0)

            server_tail = ""
            if http_5xx or unexpected_errors:
                server_tail = "\n--- server.log tail ---\n" + log_path.read_text(
                    encoding="utf-8", errors="replace"
                )[-4000:]
            assert not http_5xx, (
                f"Observed HTTP 5xx under concurrency ({len(http_5xx)} total): "
                f"{http_5xx[:10]}{server_tail}"
            )
            assert not unexpected_errors, (
                f"Unexpected client errors: {unexpected_errors[:10]}{server_tail}"
            )
            assert stats["uploads"] >= 4, stats
            assert stats["searches"] >= 4, stats
            assert stats["asks"] >= 4, stats
            assert lock_probe_results and all(lock_probe_results), lock_probe_results

            # Wait until every uploaded document reaches ACTIVE.
            drain_deadline = time.monotonic() + max(120.0, len(uploaded) * 0.5)
            with httpx.Client(base_url=base_url, headers=headers, timeout=30.0) as client:
                remaining = set(uploaded.keys())
                while remaining and time.monotonic() < drain_deadline:
                    still_pending: set[str] = set()
                    for doc_id in list(remaining):
                        st = client.get(f"/v4/documents/{doc_id}", params=scope)
                        assert st.status_code == 200, (doc_id, st.status_code, st.text)
                        revs = st.json().get("revisions") or []
                        if not revs or revs[0]["stage"] != "active":
                            still_pending.add(doc_id)
                    remaining = still_pending
                    if remaining:
                        time.sleep(0.5)
                assert not remaining, (
                    f"{len(remaining)} documents did not reach active: {sorted(remaining)[:10]}"
                )

                # Verify lossless text for a sample (all if <= 50, else 50 spread across workers).
                check_ids = sorted(uploaded.keys())
                if len(check_ids) > 50:
                    step_idx = max(1, len(check_ids) // 50)
                    check_ids = check_ids[::step_idx]
                for doc_id in check_ids:
                    txt_resp = client.get(f"/v4/documents/{doc_id}/text", params=scope)
                    assert txt_resp.status_code == 200, (doc_id, txt_resp.text)
                    assert txt_resp.json()["text"] == uploaded[doc_id]["content"]

            # Direct SQLite integrity check: zero lost/duplicated chunks, all mutations committed.
            db_path = storage_root / "mesa.db"
            con = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, timeout=30.0)
            try:
                rev_rows = con.execute(
                    "SELECT ci.external_id, dr.revision_id, dr.status "
                    "FROM document_revisions dr "
                    "JOIN v4_catalog_identities ci "
                    "  ON ci.kind = 'revision' AND ci.physical_id = dr.revision_id"
                ).fetchall()
                rev_map = {str(r[0]): (str(r[1]), str(r[2])) for r in rev_rows}
                assert len(rev_map) == len(uploaded)
                for doc_id, meta in uploaded.items():
                    ext_rev_id = meta["revision_id"]
                    expected_chunks = meta["chunk_count"]
                    assert ext_rev_id in rev_map, (doc_id, ext_rev_id)
                    phys_rev_id, rev_status = rev_map[ext_rev_id]
                    assert rev_status == "ACTIVE", (doc_id, ext_rev_id, rev_status)
                    ordinals = [
                        int(row[0])
                        for row in con.execute(
                            "SELECT ordinal FROM source_chunks WHERE revision_id = ? "
                            "ORDER BY ordinal",
                            (phys_rev_id,),
                        ).fetchall()
                    ]
                    assert ordinals == list(range(expected_chunks)), (
                        doc_id,
                        ext_rev_id,
                        ordinals,
                        expected_chunks,
                    )
                pending_mutations = con.execute(
                    "SELECT COUNT(*) FROM memory_mutations WHERE state != 'COMMITTED'"
                ).fetchone()[0]
                assert pending_mutations == 0
                total_chunks = con.execute("SELECT COUNT(*) FROM source_chunks").fetchone()[0]
                total_mutations = con.execute("SELECT COUNT(*) FROM memory_mutations").fetchone()[0]
                expected_total_chunks = sum(m["chunk_count"] for m in uploaded.values())
                assert total_chunks == expected_total_chunks
                assert total_mutations == expected_total_chunks
                print(
                    f"[D112] duration={duration_s}s uploads={stats['uploads']} "
                    f"searches={stats['searches']} asks={stats['asks']} "
                    f"docs={len(uploaded)} chunks={total_chunks} "
                    f"lock_probes={len(lock_probe_results)} http_5xx=0"
                )
            finally:
                con.close()
        finally:
            _stop_server(server)

        # Once the server exits, the writer lock must be cleanly releasable/acquirable again.
        with StorageWriterLock.acquire(storage_root, owner="post-shutdown-check") as lock:
            assert lock.is_held_for(storage_root)


@pytest.mark.asyncio
async def test_sqlite_engine_configures_15s_busy_timeout(tmp_path: Path) -> None:
    from mesa_storage.sqlite_engine import AsyncEngine

    engine = AsyncEngine(str(tmp_path / "busy_check.db"))
    await engine.initialize()
    try:
        async with engine.connection() as db:
            async with db.execute("PRAGMA busy_timeout;") as cur:
                row = await cur.fetchone()
        assert row is not None and int(row[0]) == 15000
    finally:
        await engine.close()
