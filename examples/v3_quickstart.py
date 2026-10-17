"""V3 compatibility demo; requires an operational embedding/processing provider."""

import os
import time

import httpx

from mesa_api.schemas import MemoryInsertRequest, MemorySearchRequest
from mesa_client import MesaClient


def main() -> dict[str, str | int]:
    base = os.environ.get("MESA_BASE_URL", "http://127.0.0.1:18080")
    key = os.environ["MESA_API_KEY"]
    with httpx.Client(base_url=base, headers={"X-API-Key": key}, timeout=10) as http:
        response = http.post(
            "/v3/memory/session/start", json={"agent_id": "docs-agent"}
        )
        response.raise_for_status()
        session_id = response.json()["session_id"]
        with MesaClient(base, api_key=key) as client:
            accepted = client.insert(
                MemoryInsertRequest(
                    agent_id="docs-agent",
                    session_id=session_id,
                    content="The demo archive opens at nine.",
                    idempotency_key="docs-archive-" + session_id,
                )
            )
            deadline = time.monotonic() + 60
            while True:
                response = http.get(
                    f"/v3/memory/status/{accepted.log_id}",
                    params={"agent_id": "docs-agent"},
                )
                response.raise_for_status()
                state = response.json()["status"].lower()
                if state in {"processed", "failed", "rejected"}:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Sample raw-memory processing timed out")
                time.sleep(2.0)
            if state != "processed":
                raise RuntimeError(f"Sample failed: {state}")
            result = client.search(
                MemorySearchRequest(
                    agent_id="docs-agent",
                    session_id=session_id,
                    query="demo archive",
                    limit=5,
                )
            )
            if not any(
                "demo archive" in hit.content_payload for hit in result.retrieved_nodes
            ):
                raise RuntimeError("No scoped raw memory returned")
            print("PROCESSED; scoped V3 raw memory found")
            return {"session_id": session_id, "log_id": accepted.log_id}


if __name__ == "__main__":
    main()
