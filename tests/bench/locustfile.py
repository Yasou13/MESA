import random
import uuid

from locust import HttpUser, between, task


class MesaLoadTestUser(HttpUser):
    """
    Simulates a high-concurrency client interacting with the MESA memory engine.
    """

    # Wait between 0.5 and 2 seconds between tasks
    wait_time = between(0.5, 2.0)

    def on_start(self):
        """Called when a Locust user starts."""
        self.agent_id = f"locust-agent-{uuid.uuid4().hex[:8]}"
        self.session_id = f"locust-session-{uuid.uuid4().hex[:8]}"
        # Ensure API key is passed in headers
        self.client.headers.update(
            {
                "X-API-Key": "test-key-123",  # Note: should match MESA_API_KEY env var
                "Content-Type": "application/json",
            }
        )

    @task(3)
    def insert_memory(self):
        """
        Simulate the Hot-Path (INSERT).
        This tests the SQLite WAL concurrency limits.
        """
        payload = {
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "content": f"User {self.agent_id} learned a new fact at {random.randint(1000, 9999)}.",
            "metadata": {"source": "locust_load_test"},
        }

        with self.client.post(
            "/v3/memory/insert", json=payload, catch_response=True
        ) as response:
            if response.status_code == 202:
                response.success()
            else:
                response.failure(f"Insert failed with status: {response.status_code}")

    @task(1)
    def search_memory(self):
        """
        Simulate the Retrieval Path (SEARCH).
        This tests the Vector (LanceDB) and Graph (KuzuDB) read concurrency.
        """
        payload = {
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "query": "What are the latest facts learned?",
            "limit": 5,
        }

        with self.client.post(
            "/v3/memory/search", json=payload, catch_response=True
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Search failed with status: {response.status_code}")
