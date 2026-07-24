"""Anomaly detection and alerts for the Gateway."""

import logging
from typing import Dict

logger = logging.getLogger("MESA_Anomaly")


class AnomalyDetector:
    def __init__(self):
        # In a real implementation, this would keep track of rolling windows of metrics.
        # For MVP, we provide a placeholder.
        self.call_history: Dict[str, int] = {}

    async def analyze_call(
        self, client_id: str, tool_name: str, payload_size: int
    ) -> None:
        """Analyze a single tool call for anomalies."""
        self.call_history[client_id] = self.call_history.get(client_id, 0) + 1

        # Super simple hardcoded anomaly logic
        if payload_size > 1024 * 1024:  # 1MB payload
            logger.warning(
                f"[ANOMALY] Huge payload from client {client_id} calling {tool_name}: {payload_size} bytes"
            )

        if self.call_history[client_id] > 1000:
            logger.warning(
                f"[ANOMALY] Client {client_id} is extremely active: {self.call_history[client_id]} calls"
            )
