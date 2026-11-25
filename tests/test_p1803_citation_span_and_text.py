"""P18-03 tests: citation span offsets, migration backfill, and document text endpoint (D95, D96)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from alembic import command
from alembic.config import Config

from mesa_client.client import AsyncMesaV4Client, MesaV4Client
from mesa_mcp.adapter import MesaMCPAdapter
from mesa_mcp.configuration import MCPSettings
from mesa_mcp.v4_service import MesaHttpV4Service
from mesa_memory.api import server


async def _drain_work(dao, max_steps: int = 60) -> None:
    for _ in range(max_steps):
        work = await server._consume_combined_durable_work_once(
            dao,
            fact_extraction_service=server.state.fact_extraction_service,
            model_processing_enabled=server.state.fact_extraction_service is not None,
        )
        if not work:
            break


@pytest.fixture
def span_runtime(tmp_path, monkeypatch):
    @asynccontextmanager
    async def runtime(storage_name: str = "storage"):
        for key in (
            "MESA_MODEL_ENABLED",
            "MESA_EMBEDDING_ENABLED",
            "MESA_EXTRACTION_ENABLED",
            "MESA_EMBEDDING_MODEL",
            "MESA_EMBEDDING_DIMENSION",
            "MESA_LLM_MODEL",
        ):
            monkeypatch.delenv(key, raising=False)
        env_vars = {
            "MESA_RUNTIME_PROFILE": "combined",
            "MESA_STORAGE_ROOT": str(tmp_path / storage_name),
            "MESA_LOAD_DOTENV": "false",
            "MESA_EXTERNAL_PROVIDER_ENABLED": "false",
            "MESA_MODEL_ENABLED": "false",
            "MESA_EMBEDDING_ENABLED": "false",
            "MESA_EXTRACTION_ENABLED": "false",
            "MESA_API_KEY": "owner-key",
            "MESA_PRINCIPAL_ID": "owner-principal",
            "MESA_PRINCIPAL_STATUS": "active",
        }
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
            await access.grant_scope_role(
                "owner-principal",
                tenant_id="t1",
                workspace_id="ws1",
                dataset_id="ds1",
                role="OWNER",
            )
            await access.grant_principal_permission(
                "owner-principal", "local-agent", "SESSION_CREATE"
            )
            await access.grant_dataset_permission(
                "owner-principal", tenant_id="t1", dataset_id="ds1", permission="PURGE"
            )
            reader_key = await key_store.issue_key(principal_id="reader-principal")
            outsider_key = await key_store.issue_key(principal_id="outsider-principal")
            await access.grant_scope_role(
                "reader-principal",
                tenant_id="t1",
                workspace_id="ws1",
                dataset_id="ds1",
                role="READER",
            )
            # R25 amendment A1: sessionless calls need the agent permission.
            await access.grant_principal_permission(
                "reader-principal", "local-agent", "SESSION_CREATE"
            )

            def make_client(api_key: str) -> httpx.AsyncClient:
                return httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=server.app),
                    base_url="http://test",
                    headers={"X-API-Key": api_key},
                )

            owner = await stack.enter_async_context(make_client("owner-key"))
            reader = await stack.enter_async_context(make_client(reader_key))
            outsider = await stack.enter_async_context(make_client(outsider_key))
            yield {
                "dao": dao,
                "owner": owner,
                "reader": reader,
                "outsider": outsider,
                "reader_key": reader_key,
            }

    return runtime


@pytest.mark.asyncio
async def test_multichunk_citation_spans_and_document_text_endpoint(span_runtime) -> None:
    async with span_runtime() as env:
        dao = env["dao"]
        owner = env["owner"]
        reader = env["reader"]
        outsider = env["outsider"]
        reader_key = env["reader_key"]

        # Multi-chunk document (>512 tokens) so we get multiple chunks with non-zero offsets.
        paragraphs = [
            f"Section {i}: UniqueMarker{i} governs obligation clause number {i}. "
            + ("Detailed statutory clause text explaining compliance duties. " * 22)
            for i in range(6)
        ]
        full_content = "\n\n".join(paragraphs)

        ingest = await owner.post(
            "/v4/documents",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_id": "ds1",
                "document_id": "doc-multi",
                "title": "Multi Chunk Statute",
                "source_ref": "file://multi.txt",
                "content": full_content,
            },
        )
        assert ingest.status_code == 202, ingest.text
        ingest_body = ingest.json()
        assert ingest_body["chunk_count"] >= 3
        rev_id = ingest_body["revision_id"]
        await _drain_work(dao)

        # GET /v4/documents/{id}/text returns the exact rebuilt text for READER.
        text_resp = await reader.get(
            "/v4/documents/doc-multi/text",
            params={"tenant_id": "t1", "workspace_id": "ws1", "dataset_id": "ds1"},
        )
        assert text_resp.status_code == 200, text_resp.text
        text_body = text_resp.json()
        assert text_body["document_id"] == "doc-multi"
        assert text_body["revision_id"] == rev_id
        assert text_body["revision_number"] == 1
        assert text_body["status"] == "ACTIVE"
        assert text_body["total_length"] == len(full_content)
        assert text_body["start"] == 0
        assert text_body["end"] == len(full_content)
        assert text_body["text"] == full_content

        # Search across multiple chunks and verify text[span.start:span.end] == passage.
        for marker in ("UniqueMarker0", "UniqueMarker3", "UniqueMarker5"):
            search = await reader.post(
                "/v4/memory/search",
                json={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_ids": ["ds1"],
                    "query": f"{marker} obligation clause",
                    "limit": 10,
                },
            )
            assert search.status_code == 200, search.text
            results = search.json()["results"]
            assert results, f"no results for {marker}"
            for item in results:
                assert item["document_id"] == "doc-multi"
                assert item["revision_id"] == rev_id
                assert item["revision_number"] == 1
                assert item["chunk_id"]
                span = item["span"]
                assert isinstance(span, dict)
                assert 0 <= span["start"] < span["end"] <= len(full_content)
                passage = (
                    item["provenance"][0].get("literal_value")
                    or item["evidence_span"]
                )
                assert full_content[span["start"] : span["end"]] == passage

                # Sliced /text endpoint returns the exact same passage.
                sliced = await reader.get(
                    "/v4/documents/doc-multi/text",
                    params={
                        "tenant_id": "t1",
                        "workspace_id": "ws1",
                        "dataset_id": "ds1",
                        "start": span["start"],
                        "end": span["end"],
                    },
                )
                assert sliced.status_code == 200, sliced.text
                assert sliced.json()["text"] == passage

        # Later chunk must have span.start > 0 (proving cumulative offsets).
        last_search = await reader.post(
            "/v4/memory/search",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_ids": ["ds1"],
                "query": "UniqueMarker5",
                "limit": 5,
            },
        )
        top_last = last_search.json()["results"][0]
        assert top_last["span"]["start"] > 0

        # Context endpoint carries revision_id, chunk_id, and span on canonical_memories.
        sess = await owner.post(
            "/v4/sessions/start",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_ids": ["ds1"],
                "agent_id": "local-agent",
            },
        )
        assert sess.status_code == 201
        session_id = sess.json()["session_id"]
        ctx = await owner.get(
            f"/v4/sessions/{session_id}/context",
            params={"query": "UniqueMarker3 obligation"},
        )
        assert ctx.status_code == 200, ctx.text
        canonical = ctx.json()["canonical_memories"]
        assert canonical
        first_ctx = canonical[0]
        assert first_ctx["revision_id"] == rev_id
        assert first_ctx["chunk_id"]
        assert isinstance(first_ctx["span"], dict)
        assert (
            full_content[first_ctx["span"]["start"] : first_ctx["span"]["end"]]
            in full_content
        )

        # /v4/memory/insert chunks have span 0..len(chunk).
        raw_chunk_text = "Agent memory snippet stored via memory insert."
        raw_ins = await owner.post(
            "/v4/memory/insert",
            json={
                "session_id": session_id,
                "dataset_id": "ds1",
                "document_id": "doc-insert",
                "revision_id": "rev-insert-1",
                "chunk_id": "chunk-insert-0",
                "title": "Insert Note",
                "source_ref": "agent://note",
                "content": raw_chunk_text,
            },
        )
        assert raw_ins.status_code == 202, raw_ins.text
        await _drain_work(dao)
        ins_search = await reader.post(
            "/v4/memory/search",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_ids": ["ds1"],
                "document_ids": ["doc-insert"],
                "query": "Agent memory snippet",
            },
        )
        assert ins_search.status_code == 200
        ins_hit = ins_search.json()["results"][0]
        assert ins_hit["revision_id"] == "rev-insert-1"
        assert ins_hit["chunk_id"] == "chunk-insert-0"
        assert ins_hit["span"] == {"start": 0, "end": len(raw_chunk_text)}

        # MCP mesa_recall carries revision_id, chunk_id, and span.
        settings = MCPSettings(
            base_url="http://test",
            api_key=reader_key,
            default_tenant_id="t1",
            default_workspace_id="ws1",
            default_dataset_id="ds1",
            actor_id="local-agent",
        )
        v4_svc = MesaHttpV4Service(settings)
        v4_svc._http_client._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://test",
            headers={"X-API-Key": reader_key},
        )
        adapter = MesaMCPAdapter(AsyncMock(), settings, v4_svc)
        try:
            recalled = await adapter.mesa_recall(
                {"query": "UniqueMarker3", "dataset_ids": ["ds1"]}
            )
            assert recalled["total"] >= 1
            hit = recalled["results"][0]
            assert hit["revision_id"] == rev_id
            assert hit["chunk_id"]
            assert isinstance(hit["span"], dict)
            assert hit["span"]["start"] < hit["span"]["end"]
        finally:
            await v4_svc.close()

        # Async SDK get_document_text + Sync SDK payload check.
        async_sdk = AsyncMesaV4Client(base_url="http://test", api_key=reader_key)
        async_sdk._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://test",
            headers={"X-API-Key": reader_key},
        )
        try:
            sdk_text = await async_sdk.get_document_text(
                tenant_id="t1",
                workspace_id="ws1",
                dataset_id="ds1",
                document_id="doc-multi",
                start=0,
                end=40,
            )
            assert sdk_text["text"] == full_content[:40]
        finally:
            await async_sdk.aclose()

        # Security and error checks on /text.
        assert (
            await outsider.get(
                "/v4/documents/doc-multi/text",
                params={"tenant_id": "t1", "workspace_id": "ws1", "dataset_id": "ds1"},
            )
        ).status_code == 403
        assert (
            await reader.get(
                "/v4/documents/doc-multi/text",
                params={
                    "tenant_id": "t1",
                    "workspace_id": "ws1",
                    "dataset_id": "ds1",
                    "start": 20,
                    "end": 10,
                },
            )
        ).status_code == 422
        assert (
            await reader.get(
                "/v4/documents/missing-doc/text",
                params={"tenant_id": "t1", "workspace_id": "ws1", "dataset_id": "ds1"},
            )
        ).status_code == 404


@pytest.mark.asyncio
async def test_history_revisions_and_purged_document_text(span_runtime) -> None:
    async with span_runtime(storage_name="history_storage") as env:
        dao = env["dao"]
        owner = env["owner"]
        reader = env["reader"]

        # Load newer revision first, then older history revision (D87).
        v2_text = "Version 2 current text in force from 2024."
        v1_text = "Version 1 historical text in force from 2020."
        r2 = await owner.post(
            "/v4/documents",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_id": "ds1",
                "document_id": "doc-versioned",
                "title": "Law v2",
                "source_ref": "file://v2.txt",
                "content": v2_text,
                "metadata": {"valid_from": "2024-01-01T00:00:00Z"},
            },
        )
        assert r2.status_code == 202
        rev2_id = r2.json()["revision_id"]
        await _drain_work(dao)

        r1 = await owner.post(
            "/v4/documents",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_id": "ds1",
                "document_id": "doc-versioned",
                "title": "Law v1",
                "source_ref": "file://v1.txt",
                "content": v1_text,
                "metadata": {"valid_from": "2020-01-01T00:00:00Z"},
            },
        )
        assert r1.status_code == 202
        rev1_id = r1.json()["revision_id"]
        await _drain_work(dao)

        # Default returns ACTIVE revision (v2).
        active = await reader.get(
            "/v4/documents/doc-versioned/text",
            params={"tenant_id": "t1", "workspace_id": "ws1", "dataset_id": "ds1"},
        )
        assert active.status_code == 200
        assert active.json()["revision_id"] == rev2_id
        assert active.json()["text"] == v2_text

        # Explicit revision_id returns SUPERSEDED history revision's own text.
        hist = await reader.get(
            "/v4/documents/doc-versioned/text",
            params={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_id": "ds1",
                "revision_id": rev1_id,
            },
        )
        assert hist.status_code == 200
        assert hist.json()["revision_id"] == rev1_id
        assert hist.json()["status"] == "SUPERSEDED"
        assert hist.json()["text"] == v1_text

        # Purged document returns 409.
        purged = await owner.delete(
            "/v4/catalog/documents/doc-versioned",
            params={"tenant_id": "t1", "workspace_id": "ws1", "dataset_id": "ds1"},
        )
        assert purged.status_code == 202
        after_purge = await reader.get(
            "/v4/documents/doc-versioned/text",
            params={"tenant_id": "t1", "workspace_id": "ws1", "dataset_id": "ds1"},
        )
        assert after_purge.status_code == 409


def test_alembic_backfill_computes_exact_char_spans_for_095_store(tmp_path) -> None:
    """Upgrading a 0.9.5 store (a0d1e2f3a4b5 -> b1e2f3a4b5c6) backfills exact spans."""
    db_path = tmp_path / "legacy_095.db"
    alembic_dir = Path(__file__).resolve().parents[1] / "mesa_storage" / "alembic"
    cfg = Config()
    cfg.set_main_option("script_location", str(alembic_dir))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(cfg, "a0d1e2f3a4b5")

    chunks = [
        ("c0", 0, "Birinci parça Türkçe karakterli.\n\n"),
        ("c1", 1, "İkinci parça — madde 12 hükmü.\n\n"),
        ("c2", 2, "Üçüncü son parça."),
    ]
    full_text = "".join(text for _, _, text in chunks)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT INTO tenants (tenant_id, display_name) VALUES ('t1', 't1')")
        conn.execute(
            "INSERT INTO workspaces (workspace_id, tenant_id, name) VALUES ('w1', 't1', 'w1')"
        )
        conn.execute(
            "INSERT INTO datasets (dataset_id, tenant_id, workspace_id, name) "
            "VALUES ('ds1', 't1', 'w1', 'ds1')"
        )
        conn.execute(
            "INSERT INTO documents (document_id, tenant_id, dataset_id, title) "
            "VALUES ('d1', 't1', 'ds1', 'Legacy Doc')"
        )
        conn.execute(
            "INSERT INTO document_revisions (revision_id, tenant_id, document_id, revision_number, content_hash, status) "
            "VALUES ('r1', 't1', 'd1', 1, '0000000000000000000000000000000000000000000000000000000000000000', 'ACTIVE')"
        )
        for cid, ordinal, text in chunks:
            conn.execute(
                "INSERT INTO source_chunks "
                "(chunk_id, tenant_id, dataset_id, revision_id, ordinal, content_payload, content_hash, source_ref) "
                "VALUES (?, 't1', 'ds1', 'r1', ?, ?, 'h', 'file://legacy.txt')",
                (cid, ordinal, text),
            )
        conn.commit()
    finally:
        conn.close()

    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT chunk_id, content_payload, char_start, char_end "
            "FROM source_chunks ORDER BY ordinal"
        ).fetchall()
    finally:
        conn.close()

    expected_cursor = 0
    for (cid, payload, start, end), (_, _, orig) in zip(rows, chunks):
        assert payload == orig
        assert start == expected_cursor
        assert end == expected_cursor + len(orig)
        assert full_text[start:end] == orig
        expected_cursor = end


def test_sync_sdk_get_document_text_request() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        return httpx.Response(
            200,
            json={
                "document_id": "doc-1",
                "revision_id": "rev-1",
                "revision_number": 1,
                "status": "ACTIVE",
                "total_length": 10,
                "start": 2,
                "end": 6,
                "text": "llow",
            },
        )

    sdk = MesaV4Client(base_url="http://test", api_key="k")
    sdk._client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    try:
        resp = sdk.get_document_text(
            tenant_id="t1",
            workspace_id="ws1",
            dataset_id="ds1",
            document_id="doc-1",
            revision_id="rev-1",
            start=2,
            end=6,
        )
        assert resp["text"] == "llow"
    finally:
        sdk.close()
    assert seen[0][0] == "GET"
    assert "/v4/documents/doc-1/text" in seen[0][1]
    assert "start=2" in seen[0][1]
    assert "end=6" in seen[0][1]
    assert json.dumps(resp)
