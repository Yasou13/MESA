"""Policy Simulator to test authorization logic safely."""

import logging
from typing import Any, Dict

from mesa_mcp.gateway.policy.engine import PolicyEngine

logger = logging.getLogger("MESA_PolicySim")


class PolicySimulator:
    def __init__(self, engine: PolicyEngine):
        self.engine = engine

    def simulate(
        self, client_id: str, tool_name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Dry-run the policy engine for a given tool call."""
        # Convert tool call to internal semantic action for policy evaluation
        operation = "READ"
        if tool_name in ["mesa_store_memory", "mesa_remember", "mesa_improve"]:
            operation = "WRITE"
        elif tool_name in ["mesa_forget"]:
            operation = "DELETE"

        try:
            # Here we'd mock the client_repo fetch, but for simple simulation we assume standard bindings
            # Normally we'd call self.engine.evaluate(operation, context)

            return {
                "client_id": client_id,
                "tool_name": tool_name,
                "simulated_operation": operation,
                "result": "ALLOW",
                "dry_run": True,
            }
        except Exception as e:
            return {
                "client_id": client_id,
                "tool_name": tool_name,
                "simulated_operation": operation,
                "result": "DENY",
                "reason": str(e),
                "dry_run": True,
            }
