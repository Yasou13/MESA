import http.server
import json
import socketserver
import time
from typing import Any


class DemoHandler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self) -> None:
        content_len = int(self.headers.get("Content-Length", 0))
        post_body = b""
        if content_len:
            post_body = self.rfile.read(content_len)

        if self.path == "/v3/memory/session/start":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            response_start: dict[str, Any] = {"session_id": "sess_demo_12345"}
            self.wfile.write(json.dumps(response_start).encode("utf-8"))

        elif self.path == "/v3/demo/chat":
            data = json.loads(post_body) if post_body else {}
            query = data.get("query", "")

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()

            time.sleep(0.5)  # Simulate latency

            response: dict[str, Any] = {
                "response_text": f"[MOCK BACKEND] '{query}' başarıyla işlendi. MESA KuzuDB grafiğinde 3 düğüm (node) ve 2 kenar (edge) bulundu.",
                "latency_ms": 112.5,
                "memory_stored": True,
                "context": [
                    {
                        "content": "Kullanıcı enterprise planla ilgileniyor.",
                        "score": 0.945,
                    },
                    {
                        "content": "MESA KuzuDB property graph avoids hallucinations.",
                        "score": 0.880,
                    },
                ],
            }
            self.wfile.write(json.dumps(response).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


PORT = 8085
print(f"Starting mock backend + static server at http://localhost:{PORT}")
with ReusableTCPServer(("", PORT), DemoHandler) as httpd:
    httpd.serve_forever()
