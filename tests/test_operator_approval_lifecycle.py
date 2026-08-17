"""Public remember -> operator approval -> durable recall lifecycle proof."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from mesa_mcp.configuration import MCPSettings
from mesa_mcp.gateway.app import create_gateway_app
from mesa_mcp.gateway.approval import OperationApprovalService
from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.api import server
from mesa_memory.config import configured_embedding_identity
from mesa_memory.embedding.service import EmbeddingIdentity, EmbeddingService


class _DeterministicProvider(BaseUniversalLLMAdapter):
    model_name = "operator-approval-lifecycle"

    def complete(self, prompt: str, schema: Any = None, **_kwargs: Any) -> Any:
        if schema is not None:
            source_span = prompt.rsplit("<UNTRUSTED_SOURCE>\n", 1)[-1].split(
                "\n</UNTRUSTED_SOURCE>", 1
            )[0]
            return schema.model_validate(
                {
                    "facts": [
                        {
                            "fact_text": "MESA supports operator approval.",
                            "subject": "MESA",
                            "predicate": "SUPPORTS",
                            "object": "operator approval",
                            "confidence": 1.0,
                            "source_span": source_span,
                        }
                    ]
                }
            )
        return '{"decision":"STORE","justification":"deterministic approval proof"}'

    async def acomplete(self, prompt: str, schema: Any = None, **kwargs: Any) -> Any:
        return self.complete(prompt, schema, **kwargs)

    def embed(self, _text: str, **_kwargs: Any) -> list[float]:
        return [1.0] + [0.0] * 383

    async def aembed(self, text: str, **kwargs: Any) -> list[float]:
        return self.embed(text, **kwargs)

    def embed_batch(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return [self.embed(text, **kwargs) for text in texts]

    async def aembed_batch(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return self.embed_batch(texts, **kwargs)

    def get_token_count(self, text: str) -> int:
        return len(text.split())


def _embedding_service(provider: _DeterministicProvider) -> EmbeddingService:
    configured = configured_embedding_identity()
    return EmbeddingService(
        identity=EmbeddingIdentity(
            provider=configured.provider,
            model=configured.model,
            dimension=configured.dimension,
            version=configured.version,
            normalized=configured.normalized,
            model_revision=configured.model_revision,
        ),
        provider_fn=provider.embed,
        allow_model_loading=False,
        external_enabled=True,
    )


def _tool_result(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    envelope = response.json()
    assert envelope["isError"] is False
    return json.loads(envelope["content"][0]["text"])


async def _use_in_process_api(operation_service: Any) -> None:
    v4_client = operation_service._v4._http_client
    await v4_client._client.aclose()
    v4_client._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url="http://mesa-api",
        headers={"X-API-Key": "operator-lifecycle-key"},
        timeout=8.0,
    )


async def _wait_for_committed(
    client: httpx.AsyncClient, headers: dict[str, str], operation_id: str
) -> dict[str, Any]:
    for _ in range(150):
        response = await client.get(
            f"/mcp/v1/operations/{operation_id}", headers=headers
        )
        response.raise_for_status()
        status = response.json()
        if status["status"] == "COMMITTED":
            return status
        if status["status"] in {"FAILED", "REJECTED", "DENIED"}:
            raise AssertionError(f"approved operation failed: {status}")
        await asyncio.sleep(0.1)
    raise AssertionError("approved operation did not reach COMMITTED")


@pytest.mark.asyncio
async def test_public_remember_approval_recall_survives_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / "runtime-storage"
    gateway_db = tmp_path / "gateway.sqlite"
    provider = _DeterministicProvider()
    encryption_key = "peLOISXzSH31UTN4P47MRyW_vtDq_vE79Wksmp1r0sI="
    monkeypatch.setenv("MESA_RUNTIME_PROFILE", "combined")
    monkeypatch.setenv("MESA_STORAGE_ROOT", str(storage))
    monkeypatch.setenv("MESA_LOAD_DOTENV", "false")
    monkeypatch.setenv("MESA_MODEL_ENABLED", "true")
    monkeypatch.setenv("MESA_EXTERNAL_PROVIDER_ENABLED", "true")
    monkeypatch.setenv("MESA_EMBEDDING_DIMENSION", "384")
    monkeypatch.setenv("MESA_LLM_PROVIDER", "mock")
    monkeypatch.setenv("MESA_API_KEY", "operator-lifecycle-key")
    monkeypatch.setenv("MESA_PRINCIPAL_ID", "api-principal")
    monkeypatch.setenv("MESA_PRINCIPAL_STATUS", "active")
    monkeypatch.setattr(
        server.AdapterFactory,
        "get_adapter",
        staticmethod(lambda *args, **kwargs: provider),
    )
    monkeypatch.setattr(
        server,
        "_get_embedding_service",
        lambda **_kwargs: _embedding_service(provider),
    )
    monkeypatch.setattr(
        server.AdapterFactory,
        "get_tier3_adapters",
        staticmethod(lambda: (provider, provider)),
    )
    from mesa_memory.extraction import rebel_pipeline

    rebel_pipeline._model_holder.reset()
    monkeypatch.setattr(
        rebel_pipeline,
        "pipeline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("deterministic REBEL boundary")
        ),
    )

    settings = MCPSettings(
        base_url="http://mesa-api",
        api_key="operator-lifecycle-key",
        gateway_control_db=gateway_db,
        gateway_encryption_key=encryption_key,
        workspace_root=tmp_path,
    )
    token: str
    operation_id: str
    remembered_ids: set[str]

    async with server.lifespan(server.app):
        await server.state.dao.ensure_v4_catalog_scope(
            tenant_id="tenant-operator", workspace_id="workspace", dataset_id="dataset"
        )
        await server.state.access_control.grant_principal_permission(
            "api-principal", "memory-agent", "SESSION_CREATE"
        )
        await server.state.access_control.grant_scope_role(
            "api-principal",
            tenant_id="tenant-operator",
            workspace_id="workspace",
            dataset_id="dataset",
            role="WRITER",
        )
        await server.state.access_control.grant_control_role("operator-admin")

        gateway_app = create_gateway_app(settings)
        async with gateway_app.router.lifespan_context(gateway_app):
            operations = gateway_app.state.operation_service
            await _use_in_process_api(operations)
            await operations._middleware.client_repo.create_client(
                "operator-client", "Operator client", "codex", "memory-agent"
            )
            binding_id = await operations._middleware.client_repo.add_project_binding(
                "operator-client",
                "sha256:operator-workspace",
                "tenant-operator",
                "workspace",
                "dataset",
            )
            _credential, token = await operations._middleware.credential_repo.issue(
                "operator-client", binding_id
            )
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=gateway_app),
                base_url="http://gateway",
            ) as client:
                pending = _tool_result(
                    await client.post(
                        "/mcp/v1/tools/call",
                        headers=headers,
                        json={
                            "name": "mesa_remember",
                            "arguments": {
                                "title": "Operator lifecycle",
                                "content": "MESA operator approval survives a restart.",
                                "idempotency_key": "operator-lifecycle-remember",
                            },
                        },
                    )
                )
                assert pending["status"] == "PENDING_APPROVAL"
                operation_id = pending["operation_id"]

                decided = await OperationApprovalService(
                    engine=operations._engine,
                    access_control=server.state.access_control,
                ).decide(
                    operation_id=operation_id,
                    decision="APPROVED",
                    decided_by="operator-admin",
                    reason="verified lifecycle fixture",
                )
                assert decided["status"] == "APPROVED"
                committed = await _wait_for_committed(client, headers, operation_id)
                assert committed["mutation_id"]

                recall = _tool_result(
                    await client.post(
                        "/mcp/v1/tools/call",
                        headers=headers,
                        json={
                            "name": "mesa_recall",
                            "arguments": {"query": "operator approval restart"},
                        },
                    )
                )
                assert any(
                    "operator approval" in memory["content"]
                    for memory in recall["memories"]
                ), recall
                remembered_ids = {memory["memory_id"] for memory in recall["memories"]}

    async with server.lifespan(server.app):
        restarted_gateway = create_gateway_app(settings)
        async with restarted_gateway.router.lifespan_context(restarted_gateway):
            operations = restarted_gateway.state.operation_service
            await _use_in_process_api(operations)
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=restarted_gateway),
                base_url="http://gateway",
            ) as client:
                status = await client.get(
                    f"/mcp/v1/operations/{operation_id}", headers=headers
                )
                assert status.json()["status"] == "COMMITTED"
                recall = _tool_result(
                    await client.post(
                        "/mcp/v1/tools/call",
                        headers=headers,
                        json={
                            "name": "mesa_recall",
                            "arguments": {"query": "operator approval restart"},
                        },
                    )
                )
                assert {
                    memory["memory_id"] for memory in recall["memories"]
                } == remembered_ids
                assert any(
                    memory["content"] == "operator approval"
                    for memory in recall["memories"]
                ), recall
