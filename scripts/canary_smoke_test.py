import logging
import os
import sys
import time

from mesa_api.schemas import MemoryInsertRequest, MemorySearchRequest
from mesa_client import MesaClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Canary")


def wait_for_port(port: int, timeout: int = 45):
    import socket

    start = time.time()
    while time.time() - start < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


def run_smoke_test():
    """Basic smoke test to ensure the client can connect and execute a simple workflow."""
    try:
        logger.info("Using existing Uvicorn server on port 8000 for Canary Test...")
        api_key = os.environ.get("MESA_API_KEY")
        if not api_key:
            logger.error("MESA_API_KEY must be explicitly configured for the canary.")
            return 2

        if not wait_for_port(8000):
            logger.error("Server on port 8000 is not running.")
            return 1

        import httpx

        logger.info("Starting a new session via httpx to get RBAC rights...")
        resp = httpx.post(
            "http://127.0.0.1:8000/v3/memory/session/start",
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json",
            },
            json={"agent_id": "canary-agent"},
        )
        resp.raise_for_status()
        session_id = resp.json()["session_id"]
        logger.info(f"Session started: {session_id}")

        logger.info("Server is up. Initializing MesaClient...")
        client = MesaClient(base_url="http://127.0.0.1:8000", api_key=api_key)

        logger.info("Testing Insert (add_memory)...")
        agent_id = "canary-agent"
        content = "The canary sings at midnight in the coal mine."

        insert_resp = client.insert(
            request=MemoryInsertRequest(
                agent_id=agent_id,
                session_id=session_id,
                content=content,
                metadata={"canary_id": "c123"},
            )
        )
        logger.info(f"Insert Response: {insert_resp}")
        assert insert_resp.status == "DEFERRED", "Insert failed"

        logger.info("Testing Retrieve (search_memory)...")
        results = []
        for attempt in range(30):
            time.sleep(1)
            search_resp = client.search(
                request=MemorySearchRequest(
                    agent_id=agent_id,
                    session_id=session_id,
                    query="When does the canary sing?",
                    limit=3,
                )
            )
            logger.info(f"Search Response: {search_resp}")

            results = search_resp.retrieved_nodes
            if len(results) > 0:
                break
            logger.info("Retrying search... background task might be processing")

        assert (
            len(results) > 0
        ), "Hit@K = 0, Retrieval failed to find the inserted node after 10 seconds."

        top_result = results[0]
        assert top_result.content_payload is not None, "Payload is None"
        assert (
            "canary sings at midnight" in top_result.content_payload
        ), "Context ID / Payload mismatch!"

        logger.info(
            "Canary smoke test passed successfully. Insert + Retrieve + Hit@K>0 validated."
        )
        return 0
    except Exception as e:
        logger.error(f"Canary smoke test failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(run_smoke_test())
