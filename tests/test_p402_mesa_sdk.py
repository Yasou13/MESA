from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from mesa_client import (
    AsyncMesaV4Client,
    Mesa,
    MesaClientError,
    MesaV4Client,
)


class FakeMesaServerHandler(BaseHTTPRequestHandler):
    requests_log: list[dict[str, Any]] = []

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Quiet logs during test runs

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""
        parsed = json.loads(body) if body else {}

        req_info = {
            "method": "POST",
            "path": self.path,
            "headers": dict(self.headers),
            "body": parsed,
        }
        self.requests_log.append(req_info)

        if self.path == "/v4/catalog/workspaces":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"created","workspace_id":"default"}')
        elif self.path == "/v4/catalog/datasets":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"created","dataset_id":"default"}')
        elif self.path == "/v4/sessions/start":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"session_id":"sess-12345","status":"active"}')
        elif self.path == "/v4/documents":
            doc_id = parsed.get("document_id", "doc-test")
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = {
                "document_id": doc_id,
                "revision_id": "rev-test1234",
                "revision_number": 1,
                "chunk_count": 2,
                "mutation_ids": ["mut-1", "mut-2"],
                "duplicate": False,
            }
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        elif "/search" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = {
                "results": [
                    {
                        "memory_id": "mem-1",
                        "score": 0.95,
                        "content": "Kira bedeli ayin 15 inde odenir.",
                    }
                ]
            }
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self) -> None:
        self.requests_log.append({
            "method": "GET",
            "path": self.path,
            "headers": dict(self.headers),
        })
        if "/context" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = {
                "context": "<UNTRUSTED_MEMORY_EVIDENCE>\nKira bedeli 15'inde odenir\n</UNTRUSTED_MEMORY_EVIDENCE>",
                "context_status": "OK",
            }
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self) -> None:
        self.requests_log.append({
            "method": "DELETE",
            "path": self.path,
            "headers": dict(self.headers),
        })
        if "/v4/catalog/documents/" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"purged"}')
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def fake_server():
    FakeMesaServerHandler.requests_log = []
    server = HTTPServer(("127.0.0.1", 0), FakeMesaServerHandler)
    host, port = server.server_address
    url = f"http://{host}:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield url, FakeMesaServerHandler.requests_log
    server.shutdown()
    server.server_close()


def test_mesav4client_add_document(fake_server):
    url, logs = fake_server
    client = MesaV4Client(base_url=url, api_key="test-key")
    try:
        resp = client.add_document(
            session_id="sess-abc",
            dataset_id="default",
            document_id="doc-123",
            title="Kira Sözleşmesi",
            source_ref="file://kira.txt",
            content="Kira bedeli her ayın 15'inci günü ödenir.",
            metadata={"department": "legal"},
        )
        assert resp["document_id"] == "doc-123"
        assert resp["chunk_count"] == 2
        assert len(logs) == 1
        assert logs[0]["path"] == "/v4/documents"
        assert logs[0]["body"]["session_id"] == "sess-abc"
        assert logs[0]["body"]["document_id"] == "doc-123"
        assert logs[0]["body"]["title"] == "Kira Sözleşmesi"
        assert logs[0]["body"]["source_ref"] == "file://kira.txt"
        assert logs[0]["body"]["content"] == "Kira bedeli her ayın 15'inci günü ödenir."
        assert logs[0]["body"]["metadata"] == {"department": "legal"}
    finally:
        client.close()


@pytest.mark.asyncio
async def test_async_mesav4client_add_document(fake_server):
    url, logs = fake_server
    client = AsyncMesaV4Client(base_url=url, api_key="test-key")
    try:
        resp = await client.add_document(
            session_id="sess-async",
            dataset_id="default",
            document_id="doc-async",
            title="Async Doc",
            source_ref="file://async.txt",
            content="Async content",
        )
        assert resp["document_id"] == "doc-async"
        assert len(logs) == 1
        assert logs[0]["path"] == "/v4/documents"
        assert logs[0]["body"]["session_id"] == "sess-async"
        assert logs[0]["body"]["document_id"] == "doc-async"
    finally:
        await client.aclose()


def test_mesa_sdk_credential_resolution_order(tmp_path, monkeypatch):
    creds_dir = tmp_path / ".mesa"
    creds_dir.mkdir()
    creds_file = creds_dir / "credentials.json"
    creds_file.write_text(
        json.dumps({
            "base_url": "http://file-host:9999",
            "api_key": "file-key",
            "tenant_id": "file-tenant",
            "workspace_id": "file-workspace",
            "dataset_id": "file-dataset",
            "agent_id": "file-agent",
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("MESA_HOME", str(creds_dir))

    # 1. Without env vars or explicit args, credentials.json is used
    monkeypatch.delenv("MESA_BASE_URL", raising=False)
    monkeypatch.delenv("MESA_API_KEY", raising=False)
    m1 = Mesa()
    assert m1.base_url == "http://file-host:9999"
    assert m1.api_key == "file-key"
    assert m1.dataset_id == "file-dataset"
    assert m1.tenant_id == "file-tenant"
    assert m1.workspace_id == "file-workspace"
    assert m1.agent_id == "file-agent"
    m1.close()

    # 2. Environment variables override credentials.json
    monkeypatch.setenv("MESA_BASE_URL", "http://env-host:8888")
    monkeypatch.setenv("MESA_API_KEY", "env-key")
    m2 = Mesa()
    assert m2.base_url == "http://env-host:8888"
    assert m2.api_key == "env-key"
    assert m2.dataset_id == "file-dataset"
    m2.close()

    # 3. Explicit constructor arguments override environment variables
    m3 = Mesa(base_url="http://arg-host:7777", api_key="arg-key", dataset_id="arg-dataset")
    assert m3.base_url == "http://arg-host:7777"
    assert m3.api_key == "arg-key"
    assert m3.dataset_id == "arg-dataset"
    m3.close()

    # 4. If no key is found anywhere, raises MesaClientError mentioning mesa start
    monkeypatch.delenv("MESA_HOME", raising=False)
    monkeypatch.delenv("MESA_API_KEY", raising=False)
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    monkeypatch.setenv("MESA_HOME", str(empty_home))
    with pytest.raises(MesaClientError) as exc_info:
        Mesa()
    assert "mesa start" in str(exc_info.value).lower()


def test_mesa_sdk_first_use_and_methods(fake_server):
    url, logs = fake_server
    with Mesa(base_url=url, api_key="test-key", dataset_id="legal") as mesa:
        # First call triggers ensure_session: create_workspace, create_dataset, start_session
        content = "Kira bedeli her ayın 15'inci günü ödenir."
        source = "file://kira.txt"
        res = mesa.add(content, source=source)

        expected_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]
        expected_doc_id = f"doc-{expected_hash}"

        assert res["document_id"] == expected_doc_id
        # Check logs: 1. workspace, 2. dataset, 3. session, 4. add_document
        paths = [log["path"] for log in logs]
        assert "/v4/catalog/workspaces" in paths
        assert "/v4/catalog/datasets" in paths
        assert "/v4/sessions/start" in paths
        assert "/v4/documents" in paths

        # Second call reuses existing session (no new create_workspace/start_session)
        num_logs_before = len(logs)
        search_res = mesa.search("kira", limit=5)
        assert len(search_res) == 1
        assert search_res[0]["memory_id"] == "mem-1"
        assert len(logs) == num_logs_before + 1
        assert logs[-1]["path"] == "/v4/memory/search"
        assert logs[-1]["body"]["session_id"] == mesa._session_id
        assert logs[-1]["body"]["query"] == "kira"

        # Context returns wrapped string
        ctx = mesa.context("kira ne zaman ödenir?", max_tokens=1500)
        assert "<UNTRUSTED_MEMORY_EVIDENCE>" in ctx

        # Delete calls purge_document
        del_res = mesa.delete(expected_doc_id)
        assert del_res["status"] == "purged"
        assert any(
            log["method"] == "DELETE" and expected_doc_id in log["path"]
            for log in logs
        )
