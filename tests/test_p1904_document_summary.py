"""P19-04 tests: single-document summary across HTTP, SDK, CLI and MCP (D105)."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from mesa_client.client import AsyncMesaV4Client, Mesa, MesaV4Client
from mesa_mcp import cli
from mesa_mcp.adapter import MesaMCPAdapter
from mesa_mcp.configuration import MCPSettings
from mesa_mcp.local_cli import handle_local_command
from mesa_mcp.v4_service import MesaHttpV4Service
from mesa_memory.api import server
from mesa_memory.security.untrusted_memory import TAG_CLOSE, TAG_OPEN
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
def summary_runtime(tmp_path, monkeypatch):
    @asynccontextmanager
    async def runtime(
        *,
        llm_model: str = "",
        llm_base_url: str | None = None,
        context_window_limit: int | None = None,
    ):
        for key in (
            "MESA_MODEL_ENABLED",
            "MESA_EMBEDDING_ENABLED",
            "MESA_EXTRACTION_ENABLED",
            "MESA_EMBEDDING_MODEL",
            "MESA_EMBEDDING_DIMENSION",
            "MESA_LLM_MODEL",
            "MESA_LLM_BASE_URL",
            "MESA_LLM_API_KEY",
            "CONTEXT_WINDOW_LIMIT",
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
        if context_window_limit is not None:
            env_vars["CONTEXT_WINDOW_LIMIT"] = str(context_window_limit)
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
                tenant_id="t1", workspace_id="ws1", dataset_id="ds1"
            )
            reader_key = await key_store.issue_key(principal_id="reader-principal")
            outsider_key = await key_store.issue_key(principal_id="outsider-principal")

            await access.grant_scope_role(
                "owner-principal",
                tenant_id="t1",
                workspace_id="ws1",
                dataset_id="ds1",
                role="OWNER",
            )
            await access.grant_scope_role(
                "reader-principal",
                tenant_id="t1",
                workspace_id="ws1",
                dataset_id="ds1",
                role="READER",
            )
            await access.grant_principal_permission(
                "owner-principal", "local-agent", "SESSION_CREATE"
            )
            await access.grant_principal_permission(
                "reader-principal", "local-agent", "SESSION_CREATE"
            )

            transport = httpx.ASGITransport(app=server.app)
            client = await stack.enter_async_context(
                httpx.AsyncClient(transport=transport, base_url="http://testserver")
            )
            yield {
                "client": client,
                "dao": dao,
                "owner_headers": {"X-API-Key": "owner-bootstrap-key"},
                "reader_headers": {"X-API-Key": reader_key},
                "outsider_headers": {"X-API-Key": outsider_key},
            }

    return runtime


@pytest.mark.asyncio
async def test_summary_short_doc_one_call_and_history_revision(
    summary_runtime,
) -> None:
    seen_prompts: list[str] = []

    def handler(
        _path: str, _headers: dict[str, str], body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        user_msg = body["messages"][-1]["content"]
        seen_prompts.append(user_msg)
        if "10 percent" in user_msg:
            return 200, chat_completion_payload(
                "Summary (Article 1): rate is 10 percent.",
                model="fake-sum-llm",
                prompt_tokens=20,
                completion_tokens=9,
            )
        return 200, chat_completion_payload(
            "Summary (Article 1): rate is 20 percent.",
            model="fake-sum-llm",
            prompt_tokens=22,
            completion_tokens=9,
        )

    with FakeOpenAIServer(handler) as fake_llm:
        async with summary_runtime(
            llm_model="fake-sum-llm", llm_base_url=fake_llm.base_url
        ) as rt:
            client = rt["client"]
            r1 = await client.post(
                "/v4/documents",
                headers=rt["owner_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_id": "ds1",
                    "document_id": "doc-law",
                    "title": "Sample Law",
                    "source_ref": "file://law.txt",
                    "content": "Article 1: The statutory fee rate is 10 percent.",
                    "metadata": {"valid_from": "2020-01-01T00:00:00Z"},
                },
            )
            assert r1.status_code == 202
            rev1_id = r1.json()["revision_id"]
            await _drain_all_work(rt["dao"])

            r2 = await client.post(
                "/v4/documents",
                headers=rt["owner_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_id": "ds1",
                    "document_id": "doc-law",
                    "title": "Sample Law",
                    "source_ref": "file://law.txt",
                    "content": "Article 1: The statutory fee rate is 20 percent.",
                    "metadata": {"valid_from": "2024-01-01T00:00:00Z"},
                },
            )
            assert r2.status_code == 202
            rev2_id = r2.json()["revision_id"]
            await _drain_all_work(rt["dao"])

            # Default summarizes ACTIVE revision (rev 2) in 1 call
            sum_active = await client.post(
                "/v4/documents/doc-law/summary",
                headers=rt["reader_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_id": "ds1",
                },
            )
            assert sum_active.status_code == 200, sum_active.text
            body_active = sum_active.json()
            assert body_active["revision_id"] == rev2_id
            assert body_active["revision_number"] == 2
            assert body_active["status"] == "ACTIVE"
            assert body_active["parts"] == 1
            assert body_active["llm_calls"] == 1
            assert "Article 1" in body_active["summary"]
            assert "20 percent" in body_active["summary"]
            assert TAG_OPEN in seen_prompts[-1] and TAG_CLOSE in seen_prompts[-1]

            # Historical revision_id summarizes rev 1's own text
            sum_hist = await client.post(
                "/v4/documents/doc-law/summary",
                headers=rt["reader_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_id": "ds1",
                    "revision_id": rev1_id,
                },
            )
            assert sum_hist.status_code == 200
            body_hist = sum_hist.json()
            assert body_hist["revision_id"] == rev1_id
            assert body_hist["revision_number"] == 1
            assert body_hist["status"] == "SUPERSEDED"
            assert body_hist["parts"] == 1
            assert body_hist["llm_calls"] == 1
            assert "10 percent" in body_hist["summary"]

            # Unauthorized principal gets 403
            denied = await client.post(
                "/v4/documents/doc-law/summary",
                headers=rt["outsider_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_id": "ds1",
                },
            )
            assert denied.status_code == 403


@pytest.mark.asyncio
async def test_summary_long_doc_parts_combine_in_order_and_over_8_parts_422(
    summary_runtime,
) -> None:
    call_log: list[str] = []

    def handler(
        _path: str, _headers: dict[str, str], body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        user_msg = body["messages"][-1]["content"]
        call_log.append(user_msg)
        if "Ordered Part Summaries" in user_msg:
            return 200, chat_completion_payload(
                "Combined summary covering Articles 1 through 3.",
                model="fake-sum-llm",
                prompt_tokens=30,
                completion_tokens=10,
            )
        return 200, chat_completion_payload(
            f"Part summary #{len(call_log)}.",
            model="fake-sum-llm",
            prompt_tokens=20,
            completion_tokens=6,
        )

    # Small context_window_limit=160 -> part_budget=120 tokens
    with FakeOpenAIServer(handler) as fake_llm:
        async with summary_runtime(
            llm_model="fake-sum-llm",
            llm_base_url=fake_llm.base_url,
            context_window_limit=160,
        ) as rt:
            client = rt["client"]
            # Multi-part document (~250 tokens -> 3 parts + 1 combine = 4 calls)
            part_blocks = [
                f"Article {i}: " + ("Detailed statutory clause text regarding compliance obligations. " * 9)
                for i in range(1, 4)
            ]
            long_text = "\n\n".join(part_blocks)
            r_long = await client.post(
                "/v4/documents",
                headers=rt["owner_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_id": "ds1",
                    "document_id": "doc-long",
                    "title": "Long Law",
                    "source_ref": "file://long.txt",
                    "content": long_text,
                },
            )
            assert r_long.status_code == 202
            await _drain_all_work(rt["dao"])

            sum_long = await client.post(
                "/v4/documents/doc-long/summary",
                headers=rt["reader_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_id": "ds1",
                },
            )
            assert sum_long.status_code == 200, sum_long.text
            body = sum_long.json()
            assert 2 <= body["parts"] <= 8
            assert body["llm_calls"] == body["parts"] + 1
            assert len(call_log) == body["parts"] + 1
            # Verify ordered part calls followed by 1 combine call
            for idx in range(body["parts"]):
                assert f"Document Part {idx + 1}/{body['parts']}" in call_log[idx]
            assert "Ordered Part Summaries" in call_log[-1]
            assert body["usage"]["prompt_tokens"] == body["parts"] * 20 + 30
            assert "Articles 1 through 3" in body["summary"]

            # Too-long document (> 8 parts) -> HTTP 422 and 0 LLM calls
            call_log.clear()
            huge_blocks = [
                f"Article {i}: " + ("Extensive regulatory text with procedural deadlines and penalties. " * 12)
                for i in range(1, 12)
            ]
            r_huge = await client.post(
                "/v4/documents",
                headers=rt["owner_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_id": "ds1",
                    "document_id": "doc-huge",
                    "title": "Huge Law",
                    "source_ref": "file://huge.txt",
                    "content": "\n\n".join(huge_blocks),
                },
            )
            assert r_huge.status_code == 202
            await _drain_all_work(rt["dao"])

            sum_huge = await client.post(
                "/v4/documents/doc-huge/summary",
                headers=rt["reader_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_id": "ds1",
                },
            )
            assert sum_huge.status_code == 422
            assert "document too long to summarize" in sum_huge.json()["detail"]
            assert len(call_log) == 0


@pytest.mark.asyncio
async def test_summary_no_llm_configured_returns_409(summary_runtime) -> None:
    async with summary_runtime(llm_model="") as rt:
        client = rt["client"]
        r_doc = await client.post(
            "/v4/documents",
            headers=rt["owner_headers"],
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_id": "ds1",
                "document_id": "doc-no-llm",
                "title": "No LLM Doc",
                "source_ref": "file://nollm.txt",
                "content": "Article 1: Text.",
            },
        )
        assert r_doc.status_code == 202
        await _drain_all_work(rt["dao"])

        resp = await client.post(
            "/v4/documents/doc-no-llm/summary",
            headers=rt["reader_headers"],
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_id": "ds1",
            },
        )
        assert resp.status_code == 409
        assert "no LLM configured" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_summary_sdk_cli_and_mcp_surfaces(monkeypatch, capsys) -> None:
    expected = {
        "document_id": "doc-1",
        "revision_id": "rev-1",
        "revision_number": 1,
        "status": "ACTIVE",
        "summary": "Article 1 establishes the notice period.",
        "parts": 1,
        "llm_calls": 1,
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        "latency_ms": 9.4,
        "model": "fake-llm",
    }

    # Sync SDK
    sync_client = MesaV4Client(base_url="http://mesa.invalid", api_key="k")
    sync_req = MagicMock(return_value=expected)
    sync_client._request = sync_req
    assert (
        sync_client.summarize_document(
            tenant_id="t1",
            workspace_id="ws1",
            dataset_id="ds1",
            document_id="doc-1",
            revision_id="rev-1",
        )
        == expected
    )
    assert sync_req.call_args.args[:2] == ("POST", "/v4/documents/doc-1/summary")
    assert sync_req.call_args.kwargs["json"]["revision_id"] == "rev-1"

    # Async SDK
    async_client = AsyncMesaV4Client(base_url="http://mesa.invalid", api_key="k")
    async_req = AsyncMock(return_value=expected)
    async_client._request = async_req
    try:
        assert (
            await async_client.summarize_document(
                tenant_id="t1",
                workspace_id="ws1",
                dataset_id="ds1",
                document_id="doc-1",
            )
            == expected
        )
    finally:
        await async_client.aclose()

    # High-level Mesa facade
    facade = Mesa(base_url="http://mesa.invalid", api_key="k")
    facade._client.summarize_document = MagicMock(return_value=expected)
    assert facade.summarize("doc-1") == expected
    facade.close()

    # CLI `mesa summarize`
    fake_mesa = MagicMock()
    fake_mesa.summarize.return_value = expected
    monkeypatch.setattr("mesa_client.Mesa", lambda: fake_mesa)
    args = cli._parser().parse_args(["summarize", "doc-1", "--revision-id", "rev-1"])
    handle_local_command(args)
    out = capsys.readouterr().out
    assert "Article 1 establishes the notice period." in out
    fake_mesa.summarize.assert_called_once_with("doc-1", revision_id="rev-1")

    # MCP `mesa_summarize`
    v4_svc = MesaHttpV4Service(MCPSettings(api_key="test"))
    v4_svc._http_client = MagicMock(summarize_document=AsyncMock(return_value=expected))
    adapter = MesaMCPAdapter(AsyncMock(), MCPSettings(api_key="test"), v4_svc)
    assert await adapter.mesa_summarize({"document_id": "doc-1", "revision_id": "rev-1"}) == expected
