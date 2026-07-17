import asyncio
import logging
import os
import subprocess
import sys
import time

import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RateLimitTest")


def wait_for_port(port: int, timeout: int = 45):
    import socket

    start = time.time()
    while time.time() - start < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


async def run_rate_limit_test():
    server_process = None
    try:
        logger.info("Starting Uvicorn server on port 8100 for Rate Limit Test...")
        env = os.environ.copy()
        env["MESA_API_KEY"] = env.get("MESA_API_KEY", "dummy-ci-key")
        env["MESA_DAILY_REQUEST_LIMIT"] = (
            "10000"  # So we don't hit the daily limit, only the per-minute slowapi limit
        )
        env["HF_HUB_OFFLINE"] = "1"

        server_process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "mesa_memory.api.server:app",
                "--port",
                "8100",
                "--host",
                "127.0.0.1",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if not wait_for_port(8100):
            logger.error("Server failed to start within timeout.")
            return 1

        logger.info("Server is up. Hitting /v3/memory/search 61 times...")

        url = "http://127.0.0.1:8100/v3/memory/search"
        headers = {"X-API-Key": "dummy-ci-key", "Content-Type": "application/json"}
        payload = {
            "agent_id": "rl-agent",
            "session_id": "rl-session",
            "query": "hello",
            "limit": 3,
        }

        async with httpx.AsyncClient() as client:
            responses = []
            for i in range(1, 63):
                resp = await client.post(url, headers=headers, json=payload)
                responses.append(resp.status_code)
                if i % 10 == 0:
                    logger.info(f"Sent {i} requests...")

        logger.info(f"First 60 responses: {responses[:60]}")
        logger.info(f"Response 61: {responses[60]}")
        logger.info(f"Response 62: {responses[61]}")

        # Depending on if there were existing requests in this minute, we might get 429 earlier, but 61 and 62 MUST be 429.
        assert responses[60] == 429, f"61st request should be 429, got {responses[60]}"
        assert responses[61] == 429, f"62nd request should be 429, got {responses[61]}"

        logger.info("Rate limit test passed successfully. 61st request returns 429.")
        return 0
    except Exception as e:
        logger.error(f"Rate limit test failed: {e}", exc_info=True)
        return 1
    finally:
        if server_process:
            logger.info("Terminating Uvicorn server...")
            server_process.terminate()
            try:
                server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_process.kill()


if __name__ == "__main__":
    sys.exit(asyncio.run(run_rate_limit_test()))
