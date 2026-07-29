from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet

from mesa_mcp.gateway.middleware import ControlPlaneMiddleware
from mesa_mcp.gateway.operations import GatewayOperationService
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


class FakeV4Service:
    def __init__(self) -> None:
        self.remember_calls = 0
        self.mutation_states: dict[str, str] = {}

    async def health(self) -> dict[str, Any]:
        return {"status": "healthy"}

    async def v4_remember(self, **_kwargs: Any) -> dict[str, Any]:
        self.remember_calls += 1
        mutation_id = f"mut_gateway_{self.remember_calls}"
        self.mutation_states[mutation_id] = "RECEIVED"
        return {"status": "accepted", "mutation_id": mutation_id}

    async def v4_mutation_status(self, mutation_id: str) -> dict[str, Any]:
        return {
            "mutation_id": mutation_id,
            "state": self.mutation_states[mutation_id],
            "failure_class": (
                "Tier3Rejected"
                if self.mutation_states[mutation_id] == "REJECTED"
                else None
            ),
        }

    async def v4_improve(self, **_kwargs: Any) -> dict[str, Any]:
        return {"mutation_id": "mut_gateway_2"}

    async def v4_forget(self, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "purged"}

    async def v4_recall(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "memory_id": "mem_1",
                "content": "The gateway owns durable operation state.",
                "memory_type": "decision",
                "score": 0.9,
                "provenance": {"mutation_id": "mut_gateway_1"},
            }
        ]


@pytest.fixture()
async def gateway(tmp_path):
    engine = AsyncEngine(str(tmp_path / "gateway.sqlite"))
    await engine.initialize()
    await initialize_schema(engine)
    middleware = ControlPlaneMiddleware(engine=engine)
    await middleware.initialize()
    await middleware.client_repo.create_client(
        "antigravity", "Antigravity", "stdio", "principal-antigravity"
    )
    await middleware.client_repo.add_project_binding(
        "antigravity", "sha256:workspace", "tenant-1", "workspace-1", "dataset-1"
    )
    fake = FakeV4Service()
    service = GatewayOperationService(
        engine=engine,
        middleware=middleware,
        v4_service=fake,  # type: ignore[arg-type]
        encryption_key=Fernet.generate_key().decode(),
    )
    try:
        handshake = await service.handshake(
            client_id="antigravity",
            payload={"workspace_fingerprint": "sha256:workspace"},
        )
        yield service, fake, handshake["connection_id"], middleware
    finally:
        await middleware.close()


@pytest.mark.asyncio
async def test_write_is_durable_pending_approval_and_idempotent(gateway) -> None:
    service, fake, connection_id, middleware = gateway
    arguments = {
        "content": "The bridge owns only local transport.",
        "idempotency_key": "idem-1",
    }

    first = await service.call_tool(
        client_id="antigravity",
        connection_id=connection_id,
        tool_name="mesa_remember",
        arguments=arguments,
    )
    second = await service.call_tool(
        client_id="antigravity",
        connection_id=connection_id,
        tool_name="mesa_remember",
        arguments=arguments,
    )

    assert first["status"] == second["status"] == "PENDING_APPROVAL"
    assert first["operation_id"] == second["operation_id"]
    assert fake.remember_calls == 0

    await middleware.approval_repo.decide_approval(
        first["approval_id"], "APPROVED", "dashboard"
    )
    assert await service.process_approved_operations() == 1
    status = await service.operation_status("antigravity", first["operation_id"])
    assert status["status"] == "PROCESSING"
    assert status["mutation_id"] == "mut_gateway_1"
    assert fake.remember_calls == 1


@pytest.mark.asyncio
async def test_approved_operation_fails_closed_when_approval_hash_differs(gateway) -> None:
    service, fake, connection_id, middleware = gateway
    pending = await service.call_tool(
        client_id="antigravity",
        connection_id=connection_id,
        tool_name="mesa_remember",
        arguments={"content": "protected", "idempotency_key": "idem-hash-mismatch"},
    )
    await middleware.approval_repo.decide_approval(
        pending["approval_id"], "APPROVED", "dashboard"
    )
    async with service._engine.transaction() as db:
        await db.execute(
            "UPDATE mcp_approval_requests SET payload_hash = ? WHERE approval_id = ?",
            ("tampered", pending["approval_id"]),
        )
        await db.commit()

    assert await service.process_approved_operations() == 1
    status = await service.operation_status("antigravity", pending["operation_id"])
    assert status["status"] == "DENIED"
    assert status["error"]["code"] == "APPROVAL_PAYLOAD_MISMATCH"
    assert fake.remember_calls == 0


@pytest.mark.asyncio
async def test_operation_status_tracks_final_v4_mutation_state(gateway) -> None:
    service, fake, connection_id, middleware = gateway

    rejected = await service.call_tool(
        client_id="antigravity",
        connection_id=connection_id,
        tool_name="mesa_remember",
        arguments={"content": "rejectable", "idempotency_key": "idem-rejected"},
    )
    await middleware.approval_repo.decide_approval(
        rejected["approval_id"], "APPROVED", "dashboard"
    )
    await service.process_approved_operations()
    assert (
        await service.operation_status("antigravity", rejected["operation_id"])
    )["status"] == "PROCESSING"

    fake.mutation_states["mut_gateway_1"] = "REJECTED"
    rejected_status = await service.operation_status(
        "antigravity", rejected["operation_id"]
    )
    assert rejected_status["status"] == "REJECTED"
    assert rejected_status["error"]["code"] == "MUTATION_REJECTED"

    committed = await service.call_tool(
        client_id="antigravity",
        connection_id=connection_id,
        tool_name="mesa_remember",
        arguments={"content": "committable", "idempotency_key": "idem-committed"},
    )
    await middleware.approval_repo.decide_approval(
        committed["approval_id"], "APPROVED", "dashboard"
    )
    await service.process_approved_operations()
    fake.mutation_states["mut_gateway_2"] = "COMMITTED"
    assert (
        await service.operation_status("antigravity", committed["operation_id"])
    )["status"] == "COMMITTED"


@pytest.mark.asyncio
async def test_idempotency_key_cannot_be_reused_for_another_payload(gateway) -> None:
    service, _, connection_id, _ = gateway
    await service.call_tool(
        client_id="antigravity",
        connection_id=connection_id,
        tool_name="mesa_remember",
        arguments={"content": "one", "idempotency_key": "idem-conflict"},
    )
    with pytest.raises(Exception, match="idempotency_key payload mismatch"):
        await service.call_tool(
            client_id="antigravity",
            connection_id=connection_id,
            tool_name="mesa_remember",
            arguments={"content": "two", "idempotency_key": "idem-conflict"},
        )


@pytest.mark.asyncio
async def test_gateway_write_rejects_secret_before_creating_operation(gateway) -> None:
    service, _fake, connection_id, _middleware = gateway
    with pytest.raises(Exception, match="secret"):
        await service.call_tool(
            client_id="antigravity",
            connection_id=connection_id,
            tool_name="mesa_remember",
            arguments={
                "content": "Bearer do-not-store-this-secret-token-value",
                "idempotency_key": "idem-secret",
            },
        )


@pytest.mark.asyncio
async def test_recall_uses_typed_context_and_singleflight(gateway) -> None:
    service, _, connection_id, _ = gateway
    first, second = await __import__("asyncio").gather(
        service.call_tool(
            client_id="antigravity",
            connection_id=connection_id,
            tool_name="mesa_recall",
            arguments={"query": "ownership", "mode": "context"},
        ),
        service.call_tool(
            client_id="antigravity",
            connection_id=connection_id,
            tool_name="mesa_recall",
            arguments={"query": "ownership", "mode": "context"},
        ),
    )
    assert first["context_text"].startswith("[decision:mem_1]")
    assert {first["cache_status"], second["cache_status"]} <= {"MISS", "HIT"}
