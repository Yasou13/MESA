"""Launch the real stdio entry point from an empty client working directory."""

from __future__ import annotations

import json
import sys
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.fixture
def mesa_http():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body, self.headers.get("X-API-Key")))
            if self.path == "/v4/sessions/start":
                result = {"session_id": "session-stdio"}
            elif self.path == "/v4/catalog/documents":
                result = {"document_id": body["document_id"]}
            elif self.path == "/v4/memory/insert":
                result = {"status": "accepted", "mutation_id": "mutation-stdio"}
            elif self.path == "/v4/memory/search":
                result = {
                    "results": [
                        {
                            "document_id": "doc-stdio",
                            "chunk_id": "chunk-stdio",
                            "chunk_text": "stdio evidence",
                            "score": 1.0,
                        }
                    ],
                    "degraded_sources": ["vector"],
                }
            else:
                self.send_error(404)
                return
            self.reply(result)

        def do_DELETE(self):
            calls.append((self.path, None, self.headers.get("X-API-Key")))
            self.reply({"status": "PURGED", "document_id": "doc-stdio"})

        def reply(self, result):
            data = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.asyncio
@pytest.mark.parametrize("check", ["remember", "recall", "forget", "invalid", "files"])
async def test_stdio_tools_need_no_local_control_plane(tmp_path, mesa_http, check):
    base_url, calls = mesa_http
    params = StdioServerParameters(
        command=sys.executable,
        args=["-B", "-m", "mesa_mcp.server"],
        cwd=tmp_path,
        env={
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "MESA_BASE_URL": base_url,
            "MESA_API_KEY": "stdio-test-key",
            "MESA_TRANSPORT": "stdio",
            "MESA_TENANT_ID": "tenant-stdio",
            "MESA_WORKSPACE_ID": "default",
            "MESA_DATASET_ID": "main",
            "MESA_ACTOR_ID": "agent-stdio",
        },
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(
            read, write, read_timeout_seconds=timedelta(seconds=15)
        ) as session:
            await session.initialize()
            remembered = await session.call_tool(
                "mesa_remember", {"content": "stdio evidence"}
            )
            recalled = await session.call_tool(
                "mesa_recall", {"query": "stdio evidence"}
            )
            forgotten = await session.call_tool(
                "mesa_forget", {"document_id": "doc-stdio"}
            )
            invalid = await session.call_tool("mesa_remember", {"content": ""})

    remember = json.loads(remembered.content[0].text)
    recall = json.loads(recalled.content[0].text)
    forget = json.loads(forgotten.content[0].text)
    error = json.loads(invalid.content[0].text)
    if check == "remember":
        assert remember.get("status") == "accepted", remember
        assert remembered.isError is False
        assert remember["document_id"]
    elif check == "recall":
        assert recall.get("total") == 1, recall
        assert recalled.isError is False
        assert "stdio evidence" in recall["results"][0]["content"]
    elif check == "forget":
        assert forget.get("status") == "PURGED", forget
        assert forgotten.isError is False
    elif check == "invalid":
        assert error["error"]["code"] == "INVALID_ARGUMENT", error
        assert invalid.isError is True
    else:
        assert list(tmp_path.rglob("*")) == []
    if check != "files":
        assert calls and all(key == "stdio-test-key" for _, _, key in calls)
        assert sum(path == "/v4/memory/insert" for path, _, _ in calls) == 1
