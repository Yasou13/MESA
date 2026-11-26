"""P19-03 tests: POST /v1/chat/completions with official OpenAI client and SDK tool schemas (D104)."""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import httpx
import openai
import pytest

from mesa_client import (
    MESA_OPENAI_TOOLS,
    AsyncMesaV4Client,
    Mesa,
    MesaV4Client,
    MesaValidationError,
    aexecute_tool_call,
    execute_tool_call,
    get_openai_tools,
)
from mesa_memory.api import server
from tests.fake_openai_server import FakeOpenAIServer, chat_completion_payload


async def _drain_all_work(dao, max_steps: int = 30) -> None:
    for _ in range(max_steps):
        work = await server._consume_combined_durable_work_once(
            dao,
            fact_extraction_service=server.state.fact_extraction_service,
            model_processing_enabled=server.state.fact_extraction_service is not None,
        )
        if not work:
            break


@pytest.fixture
def chat_runtime(tmp_path, monkeypatch):
    @asynccontextmanager
    async def runtime(*, llm_model: str = "", llm_base_url: str | None = None):
        for key in (
            "MESA_MODEL_ENABLED",
            "MESA_EMBEDDING_ENABLED",
            "MESA_EXTRACTION_ENABLED",
            "MESA_EMBEDDING_MODEL",
            "MESA_EMBEDDING_DIMENSION",
            "MESA_LLM_MODEL",
            "MESA_LLM_BASE_URL",
            "MESA_LLM_API_KEY",
            "MESA_TENANT_ID",
            "MESA_WORKSPACE_ID",
            "MESA_DATASET_ID",
        ):
            monkeypatch.delenv(key, raising=False)
        env_vars = {
            "MESA_RUNTIME_PROFILE": "combined",
            "MESA_STORAGE_ROOT": str(tmp_path / "storage"),
            "MESA_LOAD_DOTENV": "false",
            "MESA_EXTERNAL_PROVIDER_ENABLED": "false",
            "MESA_MODEL_ENABLED": "false",
            "MESA_EMBEDDING_ENABLED": "false",
            "MESA_EXTRACTION_ENABLED": "false",
            "MESA_API_KEY": "owner-bootstrap-key",
            "MESA_PRINCIPAL_ID": "owner-principal",
            "MESA_PRINCIPAL_STATUS": "active",
        }
        if llm_model:
            env_vars["MESA_LLM_MODEL"] = llm_model
        if llm_base_url:
            env_vars["MESA_LLM_BASE_URL"] = llm_base_url
        for key, value in env_vars.items():
            monkeypatch.setenv(key, value)

        async def hold_polling(*args, **kwargs):
            await asyncio.Event().wait()

        monkeypatch.setattr(server, "_run_combined_durable_consumer", hold_polling)

        async with AsyncExitStack() as stack:
            await stack.enter_async_context(server.lifespan(server.app))
            dao = server.state.dao
            access = server.state.access_control
            key_store = server.state.api_key_store

            await dao.ensure_v4_catalog_scope(
                tenant_id="local", workspace_id="default", dataset_id="default"
            )
            await dao.ensure_v4_catalog_scope(
                tenant_id="t1", workspace_id="ws1", dataset_id="ds1"
            )
            reader_key = await key_store.issue_key(principal_id="reader-principal")
            outsider_key = await key_store.issue_key(principal_id="outsider-principal")

            for tid, wid, did in (
                ("local", "default", "default"),
                ("t1", "ws1", "ds1"),
            ):
                await access.grant_scope_role(
                    "owner-principal",
                    tenant_id=tid,
                    workspace_id=wid,
                    dataset_id=did,
                    role="OWNER",
                )
                await access.grant_scope_role(
                    "reader-principal",
                    tenant_id=tid,
                    workspace_id=wid,
                    dataset_id=did,
                    role="READER",
                )
            await access.grant_principal_permission(
                "owner-principal", "local-agent", "SESSION_CREATE"
            )
            await access.grant_principal_permission(
                "reader-principal", "local-agent", "SESSION_CREATE"
            )

            transport = httpx.ASGITransport(app=server.app)
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(transport=transport, base_url="http://testserver")
            )
            yield {
                "http_client": http_client,
                "dao": dao,
                "owner_key": "owner-bootstrap-key",
                "reader_key": reader_key,
                "outsider_key": outsider_key,
            }

    return runtime


@pytest.mark.asyncio
async def test_openai_client_non_streaming_streaming_and_scope_errors(
    chat_runtime,
) -> None:
    def handler(
        _path: str, _headers: dict[str, str], body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        user_text = body["messages"][-1]["content"]
        assert "termination notice" in user_text.lower()
        return 200, chat_completion_payload(
            json.dumps(
                {
                    "answer": "Termination notice is 30 days.",
                    "citations": [1],
                    "not_found": False,
                }
            ),
            model="fake-chat-llm",
            prompt_tokens=30,
            completion_tokens=10,
        )

    with FakeOpenAIServer(handler) as fake_llm:
        async with chat_runtime(
            llm_model="fake-chat-llm", llm_base_url=fake_llm.base_url
        ) as rt:
            http_client = rt["http_client"]
            doc_text = "The termination notice period for all leases is 30 days."
            r_doc = await http_client.post(
                "/v4/documents",
                headers={"X-API-Key": rt["owner_key"]},
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_id": "ds1",
                    "document_id": "doc-lease",
                    "title": "Lease Terms",
                    "source_ref": "file://lease.txt",
                    "content": doc_text,
                },
            )
            assert r_doc.status_code == 202
            await _drain_all_work(rt["dao"])

            # Official OpenAI AsyncOpenAI client talking directly to MESA /v1/chat/completions
            oa_client = openai.AsyncOpenAI(
                api_key=rt["reader_key"],
                base_url="http://testserver/v1",
                http_client=http_client,
                max_retries=0,
            )

            # 1. Non-streaming call
            resp = await oa_client.chat.completions.create(
                model="mesa:t1/ws1/ds1",
                messages=[
                    {"role": "system", "content": "Be helpful."},
                    {"role": "user", "content": "Older ignored question"},
                    {"role": "assistant", "content": "Older reply"},
                    {"role": "user", "content": "What is the termination notice?"},
                ],
            )
            assert resp.object == "chat.completion"
            content = resp.choices[0].message.content or ""
            assert "Termination notice is 30 days." in content
            assert "Sources:" in content
            assert "[1] doc-lease" in content
            assert resp.usage is not None
            assert resp.usage.prompt_tokens == 30
            assert resp.usage.completion_tokens == 10
            assert resp.usage.total_tokens == 40
            citations = resp.model_extra["mesa_citations"]
            assert len(citations) == 1
            assert citations[0]["document_id"] == "doc-lease"
            assert citations[0]["span"] == {"start": 0, "end": len(doc_text)}

            # 2. Streaming call -> one SSE chunk + [DONE]
            stream = await oa_client.chat.completions.create(
                model="mesa:t1/ws1/ds1",
                messages=[{"role": "user", "content": "What is the termination notice?"}],
                stream=True,
            )
            chunks = [chunk async for chunk in stream]
            assert len(chunks) == 1
            assert chunks[0].choices[0].delta.content == content
            assert chunks[0].choices[0].finish_reason == "stop"
            assert chunks[0].model_extra["mesa_citations"] == citations

            # 3. Bad model scope -> 404
            with pytest.raises(openai.NotFoundError):
                await oa_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": "hello"}],
                )
            with pytest.raises(openai.NotFoundError):
                await oa_client.chat.completions.create(
                    model="mesa:only/two",
                    messages=[{"role": "user", "content": "hello"}],
                )

            # 4. Unauthorized principal -> 403
            outsider_client = openai.AsyncOpenAI(
                api_key=rt["outsider_key"],
                base_url="http://testserver/v1",
                http_client=http_client,
                max_retries=0,
            )
            with pytest.raises(openai.PermissionDeniedError):
                await outsider_client.chat.completions.create(
                    model="mesa:t1/ws1/ds1",
                    messages=[{"role": "user", "content": "hello"}],
                )

            # 5. Missing/bad API key -> 401
            bad_auth_client = openai.AsyncOpenAI(
                api_key="invalid-key",
                base_url="http://testserver/v1",
                http_client=http_client,
                max_retries=0,
            )
            with pytest.raises(openai.AuthenticationError):
                await bad_auth_client.chat.completions.create(
                    model="mesa:t1/ws1/ds1",
                    messages=[{"role": "user", "content": "hello"}],
                )


@pytest.mark.asyncio
async def test_chat_completions_no_llm_configured_returns_409(chat_runtime) -> None:
    async with chat_runtime(llm_model="") as rt:
        oa_client = openai.AsyncOpenAI(
            api_key=rt["reader_key"],
            base_url="http://testserver/v1",
            http_client=rt["http_client"],
            max_retries=0,
        )
        with pytest.raises(openai.ConflictError, match="no LLM configured"):
            await oa_client.chat.completions.create(
                model="mesa",
                messages=[{"role": "user", "content": "hello"}],
            )


def test_openai_tool_definitions_are_valid_openai_tool_json() -> None:
    tools = get_openai_tools()
    assert tools == MESA_OPENAI_TOOLS
    names = [t["function"]["name"] for t in tools]
    assert names == ["search", "read_text", "list_documents"]
    for tool in tools:
        assert tool["type"] == "function"
        fn = tool["function"]
        assert isinstance(fn["description"], str) and fn["description"]
        params = fn["parameters"]
        assert params["type"] == "object"
        assert isinstance(params["properties"], dict)
        assert params.get("additionalProperties") is False
        # Round-trip JSON serializability
        assert json.loads(json.dumps(tool)) == tool


@pytest.mark.asyncio
async def test_tool_execution_helpers_execute_all_three_tools_against_mesa(
    chat_runtime,
) -> None:
    async with chat_runtime(llm_model="") as rt:
        http_client = rt["http_client"]
        doc_text = "Article 1: The deposit cap is three months of rent."
        r_doc = await http_client.post(
            "/v4/documents",
            headers={"X-API-Key": rt["owner_key"]},
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_id": "ds1",
                "document_id": "doc-deposit",
                "title": "Deposit Rules",
                "source_ref": "file://deposit.txt",
                "content": doc_text,
            },
        )
        assert r_doc.status_code == 202
        await _drain_all_work(rt["dao"])

        # Async helper against live ASGI server
        sdk_async = AsyncMesaV4Client(
            base_url="http://testserver",
            api_key=rt["reader_key"],
        )
        await sdk_async.aclose()
        sdk_async._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://testserver",
            headers={"X-API-Key": rt["reader_key"]},
        )

        # 1. list_documents (dict format)
        listed = await aexecute_tool_call(
            sdk_async,
            {"function": {"name": "list_documents", "arguments": "{}"}},
            tenant_id="t1",
            workspace_id="ws1",
            dataset_id="ds1",
        )
        assert any(
            d["document_id"] == "doc-deposit" for d in listed.get("documents", [])
        )

        # 2. search (OpenAI SDK ChatCompletionMessageToolCall object shape)
        search_call = SimpleNamespace(
            function=SimpleNamespace(
                name="search",
                arguments=json.dumps({"query": "deposit cap", "limit": 5}),
            )
        )
        searched = await aexecute_tool_call(
            sdk_async,
            search_call,
            tenant_id="t1",
            workspace_id="ws1",
            dataset_id="ds1",
        )
        assert len(searched["results"]) >= 1
        hit = searched["results"][0]
        assert hit["document_id"] == "doc-deposit"

        # 3. read_text (slice using span from search)
        read_call = SimpleNamespace(
            function=SimpleNamespace(
                name="read_text",
                arguments=json.dumps(
                    {
                        "document_id": "doc-deposit",
                        "start": hit["span"]["start"],
                        "end": hit["span"]["end"],
                    }
                ),
            )
        )
        read_res = await aexecute_tool_call(
            sdk_async,
            read_call,
            tenant_id="t1",
            workspace_id="ws1",
            dataset_id="ds1",
        )
        assert read_res["text"] == doc_text

        # Unknown tool rejected
        with pytest.raises(MesaValidationError, match="Unknown MESA tool"):
            await aexecute_tool_call(
                sdk_async,
                {"name": "delete_everything", "arguments": "{}"},
                tenant_id="t1",
                workspace_id="ws1",
                dataset_id="ds1",
            )

    # Sync helper with Mesa facade and MesaV4Client
    v4_sync = MesaV4Client(base_url="http://mesa.invalid", api_key="k")
    v4_sync.search = MagicMock(return_value={"results": [{"document_id": "d1"}]})
    v4_sync.get_document_text = MagicMock(return_value={"text": "hello"})
    v4_sync.list_documents = MagicMock(return_value={"documents": [{"document_id": "d1"}]})

    assert execute_tool_call(
        v4_sync,
        {"name": "search", "arguments": {"query": "q"}},
        tenant_id="t1",
        workspace_id="ws1",
        dataset_id="ds1",
    ) == {"results": [{"document_id": "d1"}]}
    assert execute_tool_call(
        v4_sync,
        {"name": "read_text", "arguments": '{"document_id": "d1", "start": 0, "end": 5}'},
        tenant_id="t1",
        workspace_id="ws1",
        dataset_id="ds1",
    ) == {"text": "hello"}
    assert execute_tool_call(
        v4_sync,
        {"name": "list_documents", "arguments": ""},
        tenant_id="t1",
        workspace_id="ws1",
        dataset_id="ds1",
    ) == {"documents": [{"document_id": "d1"}]}

    facade = Mesa(base_url="http://mesa.invalid", api_key="k")
    facade._scope_ready = True
    facade._client = v4_sync
    assert execute_tool_call(facade, {"name": "list_documents", "arguments": "{}"}) == {
        "documents": [{"document_id": "d1"}]
    }
    facade.close()
