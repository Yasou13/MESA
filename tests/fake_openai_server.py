"""Reusable local fake OpenAI-compatible HTTP server for Round 26 tests."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable


def chat_completion_payload(
    content: str,
    *,
    model: str = "fake-model",
    prompt_tokens: int = 12,
    completion_tokens: int = 8,
    completion_id: str = "chatcmpl-fake-1",
) -> dict[str, Any]:
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


HandlerFn = Callable[
    [str, dict[str, str], dict[str, Any]],
    tuple[int, dict[str, Any] | str],
]


class FakeOpenAIServer:
    """In-process local HTTP server implementing POST /v1/chat/completions."""

    def __init__(self, handler: HandlerFn) -> None:
        self._handler = handler
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def start(self) -> "FakeOpenAIServer":
        outer = self

        class _RequestHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw_body = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    parsed = json.loads(raw_body.decode("utf-8"))
                except Exception:
                    parsed = {}
                headers = {k.lower(): v for k, v in self.headers.items()}
                with outer._lock:
                    outer.requests.append(
                        {"path": self.path, "headers": headers, "body": parsed}
                    )
                status, payload = outer._handler(self.path, headers, parsed)
                if isinstance(payload, str):
                    body_bytes = payload.encode("utf-8")
                    content_type = "text/plain; charset=utf-8"
                else:
                    body_bytes = json.dumps(payload).encode("utf-8")
                    content_type = "application/json"
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(body_bytes)))
                    self.end_headers()
                    self.wfile.write(body_bytes)
                except (BrokenPipeError, ConnectionResetError):
                    return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _RequestHandler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def __enter__(self) -> "FakeOpenAIServer":
        return self.start()

    def __exit__(self, *_args: Any) -> None:
        self.stop()
