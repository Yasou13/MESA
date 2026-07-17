import http.server
import json
import socketserver

PORT = 11434


class MockOllamaHandler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(content_length)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

        if self.path == "/api/chat":
            # Mock chat response
            response = {
                "model": "qwen3:8b",
                "message": {
                    "role": "assistant",
                    "content": '{"score": 5, "reasoning": "Mocked LLM reasoning", "entities": [{"name": "test_entity", "type": "TEST"}], "triplets": [{"subject": "A", "predicate": "B", "object": "C"}]}',
                },
                "done": True,
            }
            self.wfile.write(json.dumps(response).encode())
        elif self.path == "/api/generate":
            response = {
                "model": "qwen3:8b",
                "response": '{"score": 5, "reasoning": "Mocked LLM reasoning"}',
                "done": True,
            }
            self.wfile.write(json.dumps(response).encode())
        elif self.path == "/api/embeddings":
            response = {"embedding": [0.1] * 384}
            self.wfile.write(json.dumps(response).encode())
        else:
            self.wfile.write(b"{}")

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if self.path == "/api/tags":
            self.wfile.write(b'{"models": [{"name": "qwen3:8b"}]}')
        else:
            self.wfile.write(b"Ollama is running")


if __name__ == "__main__":
    with socketserver.TCPServer(("", PORT), MockOllamaHandler) as httpd:
        print("Mock Ollama server running on port", PORT)
        httpd.serve_forever()
