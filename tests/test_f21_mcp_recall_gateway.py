from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet

pytest.importorskip("mcp")

from mesa_mcp.gateway.middleware import ControlPlaneMiddleware
from mesa_mcp.gateway.operations import GatewayOperationService
from mesa_mcp.v4_service import _typed_result
from mesa_memory.security.untrusted_memory import TAG_CLOSE, TAG_OPEN, TRUST_HEADER
from mesa_storage.schemas import initialize_schema
from mesa_storage.sqlite_engine import AsyncEngine


def test_typed_result_prefers_evidence_span_over_literal_and_fact():
    item = {
        "memory_id": "mem-1",
        "document_id": "doc-1",
        "chunk_id": "chunk-1",
        "provenance": [
            {
                "assertion_id": "mem-1",
                "chunk_id": "chunk-1",
                "evidence_span": "Kira bedeli her ayın 15'inci günü ödenir.",
                "fact_text": "kira ödeme günü: 15 gün",
                "literal_value": "15 gün",
            }
        ],
    }
    typed = _typed_result(item)
    assert typed["content"] == "Kira bedeli her ayın 15'inci günü ödenir."


def test_typed_result_full_precedence_order():
    # 1. chunk_text beats all
    c1 = _typed_result({
        "chunk_text": "chunk text",
        "content": "content text",
        "evidence_span": "evidence text",
        "provenance": [{"fact_text": "fact", "literal_value": "lit"}],
    })
    assert c1["content"] == "chunk text"

    # 2. content beats evidence_span, fact_text, literal_value
    c2 = _typed_result({
        "content": "content text",
        "evidence_span": "evidence text",
        "provenance": [{"fact_text": "fact", "literal_value": "lit"}],
    })
    assert c2["content"] == "content text"

    # 3. evidence_span beats fact_text, literal_value
    c3 = _typed_result({
        "evidence_span": "evidence text",
        "provenance": [{"fact_text": "fact", "literal_value": "lit"}],
    })
    assert c3["content"] == "evidence text"

    # 4. fact_text beats literal_value
    c4 = _typed_result({
        "provenance": [{"fact_text": "fact text", "literal_value": "lit"}],
    })
    assert c4["content"] == "fact text"

    # 5. literal_value beats fallback join
    c5 = _typed_result({
        "provenance": [{
            "subject_name": "sub",
            "predicate": "pred",
            "literal_value": "lit value",
        }],
    })
    assert c5["content"] == "lit value"


class FakeV4ForGateway:
    async def v4_recall(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "memory_id": "mem-1",
                "document_id": "doc-1",
                "chunk_id": "chunk-1",
                "content": "Raw stored evidence sentence from backend.",
                "memory_type": "decision",
                "score": 0.88,
                "provenance": {"mutation_id": "mut-1"},
            }
        ]


@pytest.fixture
async def gateway_service(tmp_path):
    engine = AsyncEngine(str(tmp_path / "gateway_test.sqlite"))
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
    service = GatewayOperationService(
        engine=engine,
        middleware=middleware,
        v4_service=FakeV4ForGateway(),  # type: ignore[arg-type]
        encryption_key=Fernet.generate_key().decode(),
    )
    handshake = await service.handshake(
        client_id="antigravity",
        payload={"workspace_fingerprint": "sha256:workspace"},
    )
    yield service, handshake["connection_id"]
    await middleware.close()


@pytest.mark.asyncio
async def test_gateway_recall_non_context_mode_wraps_memory_content(gateway_service):
    service, connection_id = gateway_service
    res = await service.call_tool(
        client_id="antigravity",
        connection_id=connection_id,
        tool_name="mesa_recall",
        arguments={"query": "evidence query", "mode": "direct"},
    )
    assert res["status"] == "SUCCESS"
    assert len(res["memories"]) == 1
    mem = res["memories"][0]
    assert mem["memory_id"] == "mem-1"
    assert mem["document_id"] == "doc-1"
    assert mem["chunk_id"] == "chunk-1"
    assert mem["score"] == 0.88
    # Content must only be inside untrusted wrapper
    assert mem["content"].startswith(TRUST_HEADER)
    assert TAG_OPEN in mem["content"]
    assert TAG_CLOSE in mem["content"]
    assert "Raw stored evidence sentence from backend." in mem["content"]
    # The raw string itself is not equal to content
    assert mem["content"] != "Raw stored evidence sentence from backend."


@pytest.mark.asyncio
async def test_gateway_recall_context_mode_wraps_memory_content(gateway_service):
    service, connection_id = gateway_service
    res = await service.call_tool(
        client_id="antigravity",
        connection_id=connection_id,
        tool_name="mesa_recall",
        arguments={"query": "evidence query", "mode": "context"},
    )
    assert res["status"] == "SUCCESS"
    assert res["context_text"].startswith(TRUST_HEADER)
    assert len(res["memories"]) == 1
    mem = res["memories"][0]
    assert mem["memory_id"] == "mem-1"
    assert mem["document_id"] == "doc-1"
    assert mem["chunk_id"] == "chunk-1"
    assert mem["score"] == 0.88
    # Content inside memories must also be wrapped
    assert mem["content"].startswith(TRUST_HEADER)
    assert TAG_OPEN in mem["content"]
    assert TAG_CLOSE in mem["content"]
    assert "Raw stored evidence sentence from backend." in mem["content"]
    assert mem["content"] != "Raw stored evidence sentence from backend."
