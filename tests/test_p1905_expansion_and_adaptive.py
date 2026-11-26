"""P19-05 tests: context expansion (neighbors/article) and adaptive planner options (D106)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from mesa_client.client import AsyncMesaV4Client, MesaV4Client, MesaValidationError
from mesa_mcp.adapter import MesaMCPAdapter
from mesa_mcp.configuration import MCPSettings
from mesa_mcp.server import _tools
from mesa_memory.api import server
from tests.fake_openai_server import FakeOpenAIServer, chat_completion_payload


async def _drain_all_work(dao, max_steps: int = 40) -> None:
    for _ in range(max_steps):
        work = await server._consume_combined_durable_work_once(
            dao,
            fact_extraction_service=server.state.fact_extraction_service,
            model_processing_enabled=server.state.fact_extraction_service is not None,
        )
        if not work:
            break


@pytest.fixture
def d106_runtime(tmp_path, monkeypatch):
    @asynccontextmanager
    async def runtime(
        *,
        llm_model: str = "",
        llm_base_url: str | None = None,
        extraction_enabled: bool = False,
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
        ):
            monkeypatch.delenv(key, raising=False)
        env_vars = {
            "MESA_RUNTIME_PROFILE": "combined",
            "MESA_STORAGE_ROOT": str(tmp_path / "storage"),
            "MESA_LOAD_DOTENV": "false",
            "MESA_EXTERNAL_PROVIDER_ENABLED": "false",
            "MESA_MODEL_ENABLED": "false",
            "MESA_EMBEDDING_ENABLED": "false",
            "MESA_EXTRACTION_ENABLED": "true" if extraction_enabled else "false",
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
                tenant_id="t1", workspace_id="ws1", dataset_id="ds1"
            )
            other_key = await key_store.issue_key(principal_id="other-principal")

            for principal, agent in (
                ("owner-principal", "local-agent"),
                ("other-principal", "other-agent"),
            ):
                await access.grant_scope_role(
                    principal,
                    tenant_id="t1",
                    workspace_id="ws1",
                    dataset_id="ds1",
                    role="OWNER",
                )
                await access.grant_principal_permission(
                    principal, agent, "SESSION_CREATE"
                )

            transport = httpx.ASGITransport(app=server.app)
            client = await stack.enter_async_context(
                httpx.AsyncClient(transport=transport, base_url="http://testserver")
            )
            yield {
                "client": client,
                "dao": dao,
                "owner_headers": {"X-API-Key": "owner-bootstrap-key"},
                "other_headers": {"X-API-Key": other_key},
            }

    return runtime


async def _start_session(
    client: httpx.AsyncClient, headers: dict[str, str], agent_id: str = "local-agent"
) -> str:
    resp = await client.post(
        "/v4/sessions/start",
        headers=headers,
        json={
            "tenant_id": "t1",
            "workspace_id": "ws1",
            "dataset_ids": ["ds1"],
            "agent_id": agent_id,
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["session_id"])


async def _insert_chunks(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    session_id: str,
    *,
    document_id: str,
    revision_id: str,
    revision_number: int = 1,
    chunks: list[tuple[str, str, dict[str, str] | None]],
    metadata: dict[str, Any] | None = None,
    supersedes_revision_id: str | None = None,
) -> None:
    await client.post(
        "/v4/catalog/documents",
        headers=headers,
        json={
            "tenant_id": "t1",
            "workspace_id": "ws1",
            "dataset_id": "ds1",
            "document_id": document_id,
            "title": document_id,
        },
    )
    rev_hash = hashlib.sha256(
        "".join(text for _, text, _ in chunks).encode("utf-8")
    ).hexdigest()
    rev_resp = await client.post(
        "/v4/catalog/revisions",
        headers=headers,
        json={
            "tenant_id": "t1",
            "workspace_id": "ws1",
            "dataset_id": "ds1",
            "document_id": document_id,
            "revision_id": revision_id,
            "revision_number": revision_number,
            "content_sha256": rev_hash,
            "supersedes_revision_id": supersedes_revision_id,
        },
    )
    assert rev_resp.status_code in {200, 201, 409}, rev_resp.text
    for ordinal, (chunk_id, text, legal_id) in enumerate(chunks):
        body: dict[str, Any] = {
            "session_id": session_id,
            "dataset_id": "ds1",
            "document_id": document_id,
            "revision_id": revision_id,
            "chunk_id": chunk_id,
            "title": document_id,
            "content": text,
            "source_ref": f"file://{document_id}.txt",
            "revision_number": revision_number,
            "chunk_ordinal": ordinal,
            "finalize_revision": ordinal == len(chunks) - 1,
            "metadata": metadata or {},
            "idempotency_key": f"idem-{revision_id}-{chunk_id}",
        }
        if legal_id is not None:
            body["legal_identity"] = legal_id
        if supersedes_revision_id is not None:
            body["supersedes_revision_id"] = supersedes_revision_id
        ins = await client.post("/v4/memory/insert", headers=headers, json=body)
        assert ins.status_code == 202, ins.text


@pytest.mark.asyncio
async def test_expand_neighbors_on_ask_and_context_keeps_original_span(
    d106_runtime,
) -> None:
    seen_prompts: list[str] = []

    def handler(
        _path: str, _headers: dict[str, str], body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        seen_prompts.append(body["messages"][-1]["content"])
        return 200, chat_completion_payload(
            json.dumps(
                {
                    "answer": "Expanded context observed.",
                    "citations": [1],
                    "not_found": False,
                }
            )
        )

    with FakeOpenAIServer(handler) as fake_llm:
        async with d106_runtime(
            llm_model="fake-llm", llm_base_url=fake_llm.base_url
        ) as rt:
            client = rt["client"]
            session_id = await _start_session(client, rt["owner_headers"])
            await _insert_chunks(
                client,
                rt["owner_headers"],
                session_id,
                document_id="doc-nb",
                revision_id="rev-nb-1",
                chunks=[
                    ("nb-c0", "CHUNK_ZERO_BEFORE. ", None),
                    ("nb-c1", "UNIQUE_ANCHOR_NEEDLE middle passage. ", None),
                    ("nb-c2", "CHUNK_TWO_AFTER. ", None),
                    ("nb-c3", "CHUNK_THREE_FAR_AWAY.", None),
                ],
            )
            await _drain_all_work(rt["dao"])

            # 1. ask with expand="none" (default)
            r_none = await client.post(
                "/v4/ask",
                headers=rt["owner_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_ids": ["ds1"],
                    "question": "UNIQUE_ANCHOR_NEEDLE",
                },
            )
            assert r_none.status_code == 200, r_none.text
            prompt_none = seen_prompts[-1]
            assert "UNIQUE_ANCHOR_NEEDLE" in prompt_none
            assert "CHUNK_ZERO_BEFORE" not in prompt_none
            assert "CHUNK_TWO_AFTER" not in prompt_none
            orig_cit = r_none.json()["citations"][0]
            assert orig_cit["chunk_id"] == "nb-c1"

            # 2. ask with expand="neighbors" -> adds c0 and c2, excludes c3, keeps c1 span
            r_nb = await client.post(
                "/v4/ask",
                headers=rt["owner_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_ids": ["ds1"],
                    "question": "UNIQUE_ANCHOR_NEEDLE",
                    "expand": "neighbors",
                },
            )
            assert r_nb.status_code == 200, r_nb.text
            prompt_nb = seen_prompts[-1]
            assert "CHUNK_ZERO_BEFORE" in prompt_nb
            assert "UNIQUE_ANCHOR_NEEDLE" in prompt_nb
            assert "CHUNK_TWO_AFTER" in prompt_nb
            assert "CHUNK_THREE_FAR_AWAY" not in prompt_nb
            nb_cit = r_nb.json()["citations"][0]
            assert nb_cit["chunk_id"] == "nb-c1"
            assert nb_cit["span"] == orig_cit["span"]

            # 3. Context route with expand="neighbors" vs default "none" (fresh read session)
            read_session_id = await _start_session(client, rt["owner_headers"])
            ctx_none = await client.get(
                f"/v4/sessions/{read_session_id}/context",
                headers=rt["owner_headers"],
                params={"query": "UNIQUE_ANCHOR_NEEDLE"},
            )
            assert ctx_none.status_code == 200
            assert "CHUNK_ZERO_BEFORE" not in ctx_none.json()["context"]
            assert ctx_none.json()["context_diagnostics"]["expand"] == "none"

            ctx_nb = await client.get(
                f"/v4/sessions/{read_session_id}/context",
                headers=rt["owner_headers"],
                params={"query": "UNIQUE_ANCHOR_NEEDLE", "expand": "neighbors"},
            )
            assert ctx_nb.status_code == 200
            ctx_body = ctx_nb.json()
            assert "CHUNK_ZERO_BEFORE" in ctx_body["context"]
            assert "CHUNK_TWO_AFTER" in ctx_body["context"]
            assert "CHUNK_THREE_FAR_AWAY" not in ctx_body["context"]
            assert ctx_body["context_diagnostics"]["expand"] == "neighbors"
            assert ctx_body["canonical_memories"][0]["chunk_id"] == "nb-c1"
            assert ctx_body["canonical_memories"][0]["span"] == orig_cit["span"]


@pytest.mark.asyncio
async def test_expansion_merges_contained_and_overlapping_source_spans(d106_runtime):
    source = "Önce ortak bölüm ve sonuç."
    spans = [(0, 17), (5, 10), (11, len(source))]
    chunks = [
        (f"overlap-c{ordinal}", source[start:end], {"law_id": "5237", "article_id": "1"})
        for ordinal, (start, end) in enumerate(spans)
    ]
    async with d106_runtime() as rt:
        session_id = await _start_session(rt["client"], rt["owner_headers"])
        await _insert_chunks(
            rt["client"],
            rt["owner_headers"],
            session_id,
            document_id="doc-overlap",
            revision_id="rev-overlap",
            chunks=chunks,
        )
        dao = rt["dao"]
        await _drain_all_work(dao)

        # The direct chunk insert route does not accept document-relative offsets.
        async with dao._sql.connection() as db:
            await db.executemany(
                "UPDATE source_chunks SET char_start = ?, char_end = ? "
                "WHERE tenant_id = 't1' AND content_payload = ?",
                [(start, end, text) for (start, end), (_, text, _) in zip(spans, chunks)],
            )
            await db.commit()

        for mode in ("neighbors", "article"):
            expanded = await dao.expand_v4_passages(
                tenant_id="t1",
                agent_id="local-agent",
                dataset_ids=["ds1"],
                results=[{"revision_id": "rev-overlap", "chunk_id": "overlap-c1"}],
                expand=mode,
            )
            assert expanded == {0: source}


@pytest.mark.asyncio
async def test_expand_article_fetches_whole_article_without_bleeding_next_article(
    d106_runtime,
) -> None:
    seen_prompts: list[str] = []

    def handler(
        _path: str, _headers: dict[str, str], body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        seen_prompts.append(body["messages"][-1]["content"])
        return 200, chat_completion_payload(
            json.dumps(
                {
                    "answer": "Article 21 requires intent.",
                    "citations": [1],
                    "not_found": False,
                }
            )
        )

    with FakeOpenAIServer(handler) as fake_llm:
        async with d106_runtime(
            llm_model="fake-llm", llm_base_url=fake_llm.base_url
        ) as rt:
            client = rt["client"]
            session_id = await _start_session(client, rt["owner_headers"])
            art21 = {"law_id": "TCK", "article_id": "21"}
            art22 = {"law_id": "TCK", "article_id": "22"}
            await _insert_chunks(
                client,
                rt["owner_headers"],
                session_id,
                document_id="doc-tck",
                revision_id="rev-tck-1",
                chunks=[
                    ("tck-c0", "ART21_PARA1 opening clause. ", art21),
                    ("tck-c1", "ART21_PARA2 middle clause. ", art21),
                    ("tck-c2", "ART21_NEEDLE_TARGET third clause. ", art21),
                    ("tck-c3", "ART21_PARA4 closing clause. ", art21),
                    ("tck-c4", "ART22_OTHER_ARTICLE negligence clause.", art22),
                ],
            )
            await _drain_all_work(rt["dao"])

            resp = await client.post(
                "/v4/ask",
                headers=rt["owner_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_ids": ["ds1"],
                    "question": "ART21_NEEDLE_TARGET",
                    "expand": "article",
                },
            )
            assert resp.status_code == 200, resp.text
            prompt = seen_prompts[-1]
            # All 4 chunks of Article 21 included (beyond +/-1 neighbors!)
            assert "ART21_PARA1" in prompt
            assert "ART21_PARA2" in prompt
            assert "ART21_NEEDLE_TARGET" in prompt
            assert "ART21_PARA4" in prompt
            # Article 22 chunk excluded!
            assert "ART22_OTHER_ARTICLE" not in prompt
            # Citation points strictly to matched chunk tck-c2
            cit = resp.json()["citations"][0]
            assert cit["chunk_id"] == "tck-c2"

            # Non-legal document with expand="article" stays unchanged
            await _insert_chunks(
                client,
                rt["owner_headers"],
                session_id,
                document_id="doc-plain",
                revision_id="rev-plain-1",
                chunks=[
                    ("pl-c0", "ALPHAONE opening sentence. ", None),
                    ("pl-c1", "OMEGANEEDLE unique target. ", None),
                    ("pl-c2", "GAMMATWO closing sentence.", None),
                ],
            )
            await _drain_all_work(rt["dao"])
            r_plain = await client.post(
                "/v4/ask",
                headers=rt["owner_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_ids": ["ds1"],
                    "question": "OMEGANEEDLE",
                    "expand": "article",
                },
            )
            assert r_plain.status_code == 200, r_plain.text
            plain_prompt = seen_prompts[-1]
            assert "OMEGANEEDLE" in plain_prompt
            assert "ALPHAONE" not in plain_prompt
            assert "GAMMATWO" not in plain_prompt


@pytest.mark.asyncio
async def test_expand_enforces_agent_and_revision_isolation(d106_runtime) -> None:
    seen_prompts: list[str] = []

    def handler(
        _path: str, _headers: dict[str, str], body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        seen_prompts.append(body["messages"][-1]["content"])
        return 200, chat_completion_payload(
            json.dumps({"answer": "Scoped answer.", "citations": [1], "not_found": False})
        )

    with FakeOpenAIServer(handler) as fake_llm:
        async with d106_runtime(
            llm_model="fake-llm", llm_base_url=fake_llm.base_url
        ) as rt:
            client = rt["client"]
            owner_sess = await _start_session(
                client, rt["owner_headers"], agent_id="local-agent"
            )
            other_sess = await _start_session(
                client, rt["other_headers"], agent_id="other-agent"
            )

            # Create one revision with ordinal 0 by other-agent and ordinal 1 by local-agent
            await client.post(
                "/v4/catalog/documents",
                headers=rt["owner_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_id": "ds1",
                    "document_id": "doc-iso",
                    "title": "Isolation Doc",
                },
            )
            await client.post(
                "/v4/catalog/revisions",
                headers=rt["owner_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_id": "ds1",
                    "document_id": "doc-iso",
                    "revision_id": "rev-iso-1",
                    "revision_number": 1,
                    "content_sha256": hashlib.sha256(b"iso-rev-1").hexdigest(),
                },
            )
            # Chunk 0 written by other-agent (MUST NOT leak into local-agent's expansion!)
            r0 = await client.post(
                "/v4/memory/insert",
                headers=rt["other_headers"],
                json={
                    "session_id": other_sess,
                    "dataset_id": "ds1",
                    "document_id": "doc-iso",
                    "revision_id": "rev-iso-1",
                    "chunk_id": "iso-c0",
                    "title": "Isolation Doc",
                    "content": "SECRET_OTHER_AGENT_NEIGHBOR",
                    "source_ref": "file://iso.txt",
                    "revision_number": 1,
                    "chunk_ordinal": 0,
                    "finalize_revision": False,
                    "idempotency_key": "idem-iso-0",
                },
            )
            assert r0.status_code == 202
            # Chunk 1 written by local-agent
            r1 = await client.post(
                "/v4/memory/insert",
                headers=rt["owner_headers"],
                json={
                    "session_id": owner_sess,
                    "dataset_id": "ds1",
                    "document_id": "doc-iso",
                    "revision_id": "rev-iso-1",
                    "chunk_id": "iso-c1",
                    "title": "Isolation Doc",
                    "content": "LOCAL_AGENT_VISIBLE_NEEDLE",
                    "source_ref": "file://iso.txt",
                    "revision_number": 1,
                    "chunk_ordinal": 1,
                    "finalize_revision": True,
                    "idempotency_key": "idem-iso-1",
                },
            )
            assert r1.status_code == 202
            await _drain_all_work(rt["dao"])

            resp = await client.post(
                "/v4/ask",
                headers=rt["owner_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_ids": ["ds1"],
                    "question": "LOCAL_AGENT_VISIBLE_NEEDLE",
                    "expand": "neighbors",
                },
            )
            assert resp.status_code == 200
            assert "LOCAL_AGENT_VISIBLE_NEEDLE" in seen_prompts[-1]
            assert "SECRET_OTHER_AGENT_NEIGHBOR" not in seen_prompts[-1]

            # Dated revision isolation: valid_at expands strictly within historical rev 1
            await _insert_chunks(
                client,
                rt["owner_headers"],
                owner_sess,
                document_id="doc-hist",
                revision_id="rev-hist-1",
                revision_number=1,
                metadata={
                    "valid_from": "2020-01-01T00:00:00Z",
                    "valid_to": "2022-12-31T23:59:59Z",
                },
                chunks=[
                    ("h1-c0", "HIST_V1_BEFORE. ", None),
                    ("h1-c1", "HIST_COMMON_NEEDLE version one. ", None),
                    ("h1-c2", "HIST_V1_AFTER.", None),
                ],
            )
            await _drain_all_work(rt["dao"])
            await _insert_chunks(
                client,
                rt["owner_headers"],
                owner_sess,
                document_id="doc-hist",
                revision_id="rev-hist-2",
                revision_number=2,
                supersedes_revision_id="rev-hist-1",
                metadata={"valid_from": "2023-01-01T00:00:00Z"},
                chunks=[
                    ("h2-c0", "HIST_V2_BEFORE. ", None),
                    ("h2-c1", "HIST_COMMON_NEEDLE version two. ", None),
                    ("h2-c2", "HIST_V2_AFTER.", None),
                ],
            )
            await _drain_all_work(rt["dao"])

            r_hist = await client.post(
                "/v4/ask",
                headers=rt["owner_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_ids": ["ds1"],
                    "question": "HIST_COMMON_NEEDLE",
                    "valid_at": "2021-06-01T00:00:00Z",
                    "expand": "neighbors",
                },
            )
            assert r_hist.status_code == 200, r_hist.text
            hist_prompt = seen_prompts[-1]
            assert "HIST_V1_BEFORE" in hist_prompt
            assert "HIST_V1_AFTER" in hist_prompt
            assert "HIST_V2_BEFORE" not in hist_prompt
            assert "HIST_V2_AFTER" not in hist_prompt

            # Uncommitted neighbor (not drained yet) is never included
            await client.post(
                "/v4/catalog/documents",
                headers=rt["owner_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_id": "ds1",
                    "document_id": "doc-uncomm",
                    "title": "doc-uncomm",
                },
            )
            await client.post(
                "/v4/catalog/revisions",
                headers=rt["owner_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_id": "ds1",
                    "document_id": "doc-uncomm",
                    "revision_id": "rev-uncomm-1",
                    "revision_number": 1,
                    "content_sha256": hashlib.sha256(b"uncomm-rev-1").hexdigest(),
                },
            )
            r_uc0 = await client.post(
                "/v4/memory/insert",
                headers=rt["owner_headers"],
                json={
                    "session_id": owner_sess,
                    "dataset_id": "ds1",
                    "document_id": "doc-uncomm",
                    "revision_id": "rev-uncomm-1",
                    "chunk_id": "uc-c0",
                    "title": "doc-uncomm",
                    "content": "COMMITTED_ANCHOR_NEEDLE. ",
                    "source_ref": "file://doc-uncomm.txt",
                    "revision_number": 1,
                    "chunk_ordinal": 0,
                    "finalize_revision": False,
                    "idempotency_key": "idem-uncomm-0",
                },
            )
            assert r_uc0.status_code == 202, r_uc0.text
            await _drain_all_work(rt["dao"])
            r_uncomm = await client.post(
                "/v4/memory/insert",
                headers=rt["owner_headers"],
                json={
                    "session_id": owner_sess,
                    "dataset_id": "ds1",
                    "document_id": "doc-uncomm",
                    "revision_id": "rev-uncomm-1",
                    "chunk_id": "uc-c1",
                    "title": "doc-uncomm",
                    "content": "UNCOMMITTED_SECRET_NEIGHBOR",
                    "source_ref": "file://doc-uncomm.txt",
                    "revision_number": 1,
                    "chunk_ordinal": 1,
                    "finalize_revision": False,
                    "idempotency_key": "idem-uncomm-1",
                },
            )
            assert r_uncomm.status_code == 202, r_uncomm.text
            # Intentionally do NOT drain work so uc-c1 remains uncommitted
            r_uc_ask = await client.post(
                "/v4/ask",
                headers=rt["owner_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_ids": ["ds1"],
                    "question": "COMMITTED_ANCHOR_NEEDLE",
                    "expand": "neighbors",
                },
            )
            assert r_uc_ask.status_code == 200
            assert "COMMITTED_ANCHOR_NEEDLE" in seen_prompts[-1]
            assert "UNCOMMITTED_SECRET_NEIGHBOR" not in seen_prompts[-1]


@pytest.mark.asyncio
async def test_adaptive_planner_on_ask_and_context_with_d102_adapter(
    d106_runtime,
) -> None:
    def handler(
        _path: str, _headers: dict[str, str], body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        resp_fmt = body.get("response_format") or {}
        schema_name = (resp_fmt.get("json_schema") or {}).get("name")
        if schema_name == "QueryExpansionOutput":
            return 200, chat_completion_payload(
                json.dumps({"queries": ["statutory penalty"]})
            )
        return 200, chat_completion_payload(
            json.dumps(
                {
                    "answer": "The statutory penalty is 5000 lira.",
                    "citations": [1],
                    "not_found": False,
                }
            )
        )

    with FakeOpenAIServer(handler) as fake_llm:
        async with d106_runtime(
            llm_model="fake-llm", llm_base_url=fake_llm.base_url
        ) as rt:
            client = rt["client"]
            await client.post(
                "/v4/documents",
                headers=rt["owner_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_id": "ds1",
                    "document_id": "doc-penalty",
                    "title": "Penalty Law",
                    "source_ref": "file://penalty.txt",
                    "content": "The statutory penalty is 5000 lira for late filing.",
                },
            )
            await client.post(
                "/v4/documents",
                headers=rt["owner_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_id": "ds1",
                    "document_id": "doc-other",
                    "title": "Other Doc",
                    "source_ref": "file://other.txt",
                    "content": "Unrelated administrative guidelines.",
                },
            )
            await _drain_all_work(rt["dao"])

            # 1. ask with retrieval_mode="single" misses lexical query "fine amount"
            r_single = await client.post(
                "/v4/ask",
                headers=rt["owner_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_ids": ["ds1"],
                    "question": "fine amount",
                    "retrieval_mode": "single",
                },
            )
            assert r_single.status_code == 200
            assert r_single.json()["not_found"] is True

            # 2. ask with retrieval_mode="adaptive" rescues doc-penalty via planner Q1
            r_adapt = await client.post(
                "/v4/ask",
                headers=rt["owner_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_ids": ["ds1"],
                    "question": "fine amount",
                    "retrieval_mode": "adaptive",
                },
            )
            assert r_adapt.status_code == 200, r_adapt.text
            b_adapt = r_adapt.json()
            assert b_adapt["not_found"] is False
            assert "5000 lira" in b_adapt["answer"]
            assert b_adapt["citations"][0]["document_id"] == "doc-penalty"

            # 3. ask with retrieval_mode="adaptive" STILL respects document_ids filter
            r_filtered = await client.post(
                "/v4/ask",
                headers=rt["owner_headers"],
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_ids": ["ds1"],
                    "document_ids": ["doc-other"],
                    "question": "fine amount",
                    "retrieval_mode": "adaptive",
                },
            )
            assert r_filtered.status_code == 200
            assert r_filtered.json()["not_found"] is True

            # 4. Context route keeps F-16 fallback when extraction is off
            session_id = await _start_session(client, rt["owner_headers"])
            ctx_off = await client.get(
                f"/v4/sessions/{session_id}/context",
                headers=rt["owner_headers"],
                params={
                    "query": "fine amount",
                    "retrieval_mode": "adaptive",
                },
            )
            assert ctx_off.status_code == 200
            assert (
                ctx_off.json()["context_diagnostics"]["planner_status"]
                == "planner_unavailable_fallback_single"
            )

        # 5. Context route adaptive planner runs on D102 adapter when extraction is enabled
        async with d106_runtime(
            llm_model="fake-llm",
            llm_base_url=fake_llm.base_url,
            extraction_enabled=True,
        ) as rt:
            client = rt["client"]
            session_id = await _start_session(client, rt["owner_headers"])
            ctx_adapt = await client.get(
                f"/v4/sessions/{session_id}/context",
                headers=rt["owner_headers"],
                params={
                    "query": "fine amount",
                    "retrieval_mode": "adaptive",
                },
            )
            assert ctx_adapt.status_code == 200
            ctx_body = ctx_adapt.json()
            assert "5000 lira" in ctx_body["context"]
            assert ctx_body["context_diagnostics"]["planner_status"] == "planner_expanded"
            assert ctx_body["context_diagnostics"]["retrieval_count"] == 2


@pytest.mark.asyncio
async def test_sdk_and_mcp_context_expand_option() -> None:
    sync_client = MesaV4Client(base_url="http://mesa.invalid", api_key="k")
    sync_req = MagicMock(return_value={})
    sync_client._request = sync_req
    sync_client.get_context(session_id="s1", query="q")
    assert "expand" not in sync_req.call_args.kwargs["params"]
    sync_client.get_context(session_id="s1", query="q", expand="article")
    assert sync_req.call_args.kwargs["params"]["expand"] == "article"
    with pytest.raises(MesaValidationError, match="expand"):
        sync_client.get_context(session_id="s1", query="q", expand="bogus")  # type: ignore[arg-type]

    async_client = AsyncMesaV4Client(base_url="http://mesa.invalid", api_key="k")
    async_req = AsyncMock(return_value={})
    async_client._request = async_req
    try:
        await async_client.get_context(session_id="s1", query="q", expand="neighbors")
        assert async_req.await_args.kwargs["params"]["expand"] == "neighbors"
    finally:
        await async_client.aclose()

    tool = next(t for t in _tools() if t.name == "mesa_get_context")
    assert tool.inputSchema["properties"]["expand"]["enum"] == [
        "none",
        "neighbors",
        "article",
    ]
    v4_mock = AsyncMock()
    v4_mock.v4_context.return_value = {"context": "ok"}
    mcp_adapter = MesaMCPAdapter(AsyncMock(), MCPSettings(api_key="k"), v4_mock)
    await mcp_adapter.get_context({"query": "q", "expand": "neighbors"})
    assert v4_mock.v4_context.await_args.kwargs["expand"] == "neighbors"
