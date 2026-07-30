from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mesa_mcp.errors import MCPError
from mesa_mcp.gateway.operations import CircuitBreaker, GatewayOperationService


class FinalityV4:
    def __init__(self, state: str) -> None:
        self.state = state

    async def v4_mutation_status(self, mutation_id: str) -> dict[str, Any]:
        return {
            "mutation_id": mutation_id,
            "state": self.state,
            "failure_class": "Tier3Rejected" if self.state == "REJECTED" else None,
            "rejection_reason": (
                "dual_llm_consensus_discard" if self.state == "REJECTED" else None
            ),
        }


def _operation(status: str = "SUBMITTED") -> dict[str, Any]:
    return {
        "operation_id": "op-1",
        "client_id": "client-1",
        "binding_id": "binding-1",
        "tool_name": "mesa_remember",
        "status": status,
        "mutation_id": "mut-1",
        "response_json": json.dumps({"mutation_id": "mut-1"}),
        "approval_id": None,
        "error_code": None,
        "error_message": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("v4_state", "expected_status", "expected_error"),
    [
        ("RECEIVED", "PROCESSING", None),
        ("RETRY_PENDING", "PROCESSING", None),
        ("REJECTED", "REJECTED", "MUTATION_REJECTED"),
        ("DEAD_LETTER", "FAILED", "MUTATION_FAILED"),
        ("COMMITTED", "COMMITTED", None),
    ],
)
async def test_operation_status_refreshes_from_v4_mutation(
    v4_state: str, expected_status: str, expected_error: str | None
) -> None:
    operation = _operation()
    service = GatewayOperationService.__new__(GatewayOperationService)
    service._v4 = FinalityV4(v4_state)  # type: ignore[assignment]
    service._breaker = CircuitBreaker()

    async def set_operation(_operation_id: str, status: str, **kwargs: Any) -> None:
        operation["status"] = status
        if kwargs.get("error_code"):
            operation["error_code"] = kwargs["error_code"]
        if kwargs.get("error_message"):
            operation["error_message"] = kwargs["error_message"]
        if kwargs.get("response"):
            operation["response_json"] = json.dumps(kwargs["response"])

    service._get_operation = AsyncMock(
        side_effect=lambda _operation_id: dict(operation)
    )
    service._set_operation = set_operation  # type: ignore[method-assign]

    result = await service.operation_status("client-1", "op-1")

    assert result["status"] == expected_status
    if expected_error:
        assert result["error"]["code"] == expected_error
    if v4_state == "REJECTED":
        assert result["rejection_reason"] == "dual_llm_consensus_discard"


@pytest.mark.asyncio
async def test_legacy_accepted_operation_is_refreshed_after_gateway_restart() -> None:
    """A restarted gateway has only the durable operation and mutation IDs."""
    operation = _operation("ACCEPTED")
    service = GatewayOperationService.__new__(GatewayOperationService)
    service._v4 = FinalityV4("COMMITTED")  # type: ignore[assignment]
    service._breaker = CircuitBreaker()

    async def set_operation(_operation_id: str, status: str, **_kwargs: Any) -> None:
        operation["status"] = status

    service._get_operation = AsyncMock(
        side_effect=lambda _operation_id: dict(operation)
    )
    service._set_operation = set_operation  # type: ignore[method-assign]

    result = await service.operation_status("client-1", "op-1")

    assert result["status"] == "COMMITTED"


@pytest.mark.asyncio
async def test_circuit_breaker_allows_only_one_half_open_probe() -> None:
    breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=0)

    async def fail() -> dict[str, Any]:
        raise MCPError("BACKEND_UNAVAILABLE", "unavailable", retryable=True)

    with pytest.raises(MCPError):
        await breaker.call(fail)

    probe_started = asyncio.Event()
    release_probe = asyncio.Event()
    probe_calls = 0

    async def probe() -> dict[str, Any]:
        nonlocal probe_calls
        probe_calls += 1
        probe_started.set()
        await release_probe.wait()
        return {"status": "ok"}

    first = asyncio.create_task(breaker.call(probe))
    await probe_started.wait()
    with pytest.raises(MCPError, match="probe is in progress"):
        await breaker.call(probe)
    assert probe_calls == 1
    release_probe.set()
    assert await first == {"status": "ok"}
    assert breaker.state == "CLOSED"
