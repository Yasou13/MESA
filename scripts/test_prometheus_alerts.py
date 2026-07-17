import os
import shutil
import subprocess
import time

import requests


def test_prometheus_alerts():
    print("Starting Prometheus Alerts Test...")

    # Ensure test environment is clean
    vector_path = "storage/vector.lance"
    vector_bak = "storage/vector.lance.bak"

    if os.path.exists(vector_bak):
        shutil.rmtree(vector_bak)

    print("1. Injecting fault (renaming LanceDB directory)...")
    if os.path.exists(vector_path):
        os.rename(vector_path, vector_bak)

    try:
        print("2. Starting Uvicorn server in background...")
        server = subprocess.Popen(
            ["venv/bin/uvicorn", "mesa_memory.api.server:app", "--port", "8002"]
        )

        print("Waiting for server to start...")
        for _ in range(30):
            try:
                if requests.get("http://localhost:8002/health/init").status_code in [
                    200,
                    503,
                ]:
                    break
            except Exception:
                pass
            time.sleep(1)

        print("3. Triggering API request to cause Saga Failure...")
        headers = {
            "X-API-Key": "mesa_prod_sec_2026_xyz",
            "Content-Type": "application/json",
        }
        payload = {
            "agent_id": "test_alert_agent",
            "messages": [{"role": "user", "content": "Trigger an alert!"}],
        }
        resp = requests.post(
            "http://localhost:8002/v3/memory", json=payload, headers=headers
        )
        print(f"Request finished with status: {resp.status_code}")

        print("4. Fetching Prometheus metrics...")
        metrics_resp = requests.get("http://localhost:8002/metrics", headers=headers)
        metrics_text = metrics_resp.text

        # Check metrics
        print("--- Metrics Evaluation ---")
        saga_fail = any(
            "saga_failure_total" in line and not line.startswith("#")
            for line in metrics_text.splitlines()
        )
        http_500 = any(
            "http_requests_total" in line
            and 'status="500"' in line
            and not line.startswith("#")
            for line in metrics_text.splitlines()
        )

        if saga_fail:
            print("✅ PASS: saga_failure_total metric is present!")
        else:
            print("❌ FAIL: saga_failure_total not found!")

        if http_500:
            print('✅ PASS: http_requests_total{status="500"} is present!')
        else:
            print('❌ FAIL: http_requests_total{status="500"} not found!')

    finally:
        print("5. Stopping server and restoring LanceDB...")
        server.terminate()
        server.wait()

        if os.path.exists(vector_bak):
            os.rename(vector_bak, vector_path)

    print("Prometheus alerts test completed.")


if __name__ == "__main__":
    test_prometheus_alerts()
