"""P18-04 tests: GET /v4/documents/{document_id}, failure messages, and /health version (D96)."""

from __future__ import annotations

import asyncio
import re
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx
import pytest

from mesa_client.client import AsyncMesaV4Client, MesaV4Client
from mesa_memory.api import server
from mesa_storage.error_messages import (
    DEFAULT_FAILURE_CODE,
    DEFAULT_FAILURE_MESSAGE,
    FAILURE_CLASS_MESSAGES,
    format_failure_error,
)
from mesa_workers import projection_worker


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
def status_runtime(tmp_path, monkeypatch):
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
            for principal in ("owner-principal", "reader-principal"):
                await access.grant_principal_permission(
                    principal, "local-agent", "SESSION_CREATE"
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


def test_every_failure_class_literal_in_code_has_a_message() -> None:
    """Every hardcoded failure_class/error_class in production code must have a plain message."""
    repo_root = Path(__file__).resolve().parents[1]
    literal_re = re.compile(
        r"""(?:failure_class|error_class)\s*=\s*["']([A-Za-z0-9_.-]+)["']"""
    )
    found: set[str] = set()
    for pkg in ("mesa_storage", "mesa_workers", "mesa_memory", "mesa_api"):
        for py_file in (repo_root / pkg).rglob("*.py"):
            if "alembic" in py_file.parts:
                continue
            text = py_file.read_text(encoding="utf-8")
            for match in literal_re.finditer(text):
                found.add(match.group(1))
    assert found, "expected to find failure_class/error_class literals in code"
    missing = sorted(code for code in found if code not in FAILURE_CLASS_MESSAGES)
    assert missing == [], f"Missing failure class messages for: {missing}"

    for code, message in FAILURE_CLASS_MESSAGES.items():
        assert message.strip(), f"Empty message for {code}"
        assert "Traceback" not in message
        assert ".py" not in message
        assert "mesa-" not in message

    # Sanitization: noisy strings or stack traces never leak into code or message.
    sanitized = format_failure_error("Traceback (most recent call last):\n  File '/a.py'")
    assert sanitized == {
        "code": DEFAULT_FAILURE_CODE,
        "message": DEFAULT_FAILURE_MESSAGE,
    }
    compound = format_failure_error("failed:internal-uuid-1234567890")
    assert compound["code"] == "failed"
    assert "internal-uuid" not in compound["message"]


@pytest.mark.asyncio
async def test_health_includes_package_version_and_document_stages(
    status_runtime, monkeypatch
) -> None:
    async with status_runtime() as env:
        dao = env["dao"]
        owner = env["owner"]
        reader = env["reader"]
        outsider = env["outsider"]
        reader_key = env["reader_key"]

        # /health includes package version.
        health_resp = await reader.get("/health")
        assert health_resp.status_code == 200, health_resp.text
        health_body = health_resp.json()
        assert health_body["version"] == server.__version__
        assert health_body["status"] == "healthy"
        assert "maintenance" in health_body
        assert "rebuild_state" in health_body

        # Multi-chunk document to observe admitted -> processing -> active -> superseded.
        paragraphs = [
            f"Paragraph {i}: Contract term {i}. "
            + ("Clause details for obligation compliance. " * 25)
            for i in range(4)
        ]
        doc_v1 = "\n\n".join(paragraphs)
        ingest1 = await owner.post(
            "/v4/documents",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_id": "ds1",
                "document_id": "doc-lifecycle",
                "title": "Lifecycle Doc",
                "source_ref": "file://life-v1.txt",
                "content": doc_v1,
                "metadata": {"valid_from": "2020-01-01T00:00:00Z"},
            },
        )
        assert ingest1.status_code == 202, ingest1.text
        chunk_count = ingest1.json()["chunk_count"]
        assert chunk_count >= 2

        # 1. Immediately after admission: stage == "admitted".
        st_admitted = await reader.get(
            "/v4/documents/doc-lifecycle",
            params={"tenant_id": "t1", "workspace_id": "ws1", "dataset_id": "ds1"},
        )
        assert st_admitted.status_code == 200, st_admitted.text
        body_admitted = st_admitted.json()
        assert body_admitted["document_id"] == "doc-lifecycle"
        assert len(body_admitted["revisions"]) == 1
        rev1 = body_admitted["revisions"][0]
        assert rev1["status"] == "PENDING"
        assert rev1["stage"] == "admitted"
        assert rev1["total_chunks"] == chunk_count
        assert rev1["chunks_by_state"] == {"RECEIVED": chunk_count}
        assert rev1["valid_from"] == "2020-01-01T00:00:00Z"
        assert rev1["error"] is None

        # 2. Partially processed (one mutation extracted, others still RECEIVED): stage == "processing".
        async with dao._sql.transaction() as db:
            await db.execute(
                "UPDATE memory_mutations SET state = 'EXTRACTED' "
                "WHERE mutation_id = ?",
                (ingest1.json()["mutation_ids"][0],),
            )
            await db.commit()
        st_proc = await reader.get(
            "/v4/documents/doc-lifecycle",
            params={"tenant_id": "t1", "workspace_id": "ws1", "dataset_id": "ds1"},
        )
        assert st_proc.status_code == 200
        rev1_proc = st_proc.json()["revisions"][0]
        assert rev1_proc["stage"] == "processing"
        assert rev1_proc["chunks_by_state"]["EXTRACTED"] == 1
        assert rev1_proc["error"] is None

        # Restore to RECEIVED and drain fully -> stage == "active".
        async with dao._sql.transaction() as db:
            await db.execute(
                "UPDATE memory_mutations SET state = 'RECEIVED' "
                "WHERE mutation_id = ?",
                (ingest1.json()["mutation_ids"][0],),
            )
            await db.commit()
        await _drain_work(dao)

        st_active = await reader.get(
            "/v4/documents/doc-lifecycle",
            params={"tenant_id": "t1", "workspace_id": "ws1", "dataset_id": "ds1"},
        )
        assert st_active.status_code == 200
        rev1_active = st_active.json()["revisions"][0]
        assert rev1_active["status"] == "ACTIVE"
        assert rev1_active["stage"] == "active"
        assert rev1_active["chunks_by_state"] == {"COMMITTED": chunk_count}
        assert rev1_active["valid_from"] == "2020-01-01T00:00:00Z"
        assert rev1_active["valid_to"] == ""
        assert rev1_active["error"] is None

        # 3. Upload v2 -> rev 1 becomes "superseded" with valid_to, rev 2 becomes "active".
        ingest2 = await owner.post(
            "/v4/documents",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_id": "ds1",
                "document_id": "doc-lifecycle",
                "title": "Lifecycle Doc v2",
                "source_ref": "file://life-v2.txt",
                "content": "Updated version two text.",
                "metadata": {"valid_from": "2024-06-01T00:00:00Z"},
            },
        )
        assert ingest2.status_code == 202
        await _drain_work(dao)

        st_two = await reader.get(
            "/v4/documents/doc-lifecycle",
            params={"tenant_id": "t1", "workspace_id": "ws1", "dataset_id": "ds1"},
        )
        assert st_two.status_code == 200
        revs = st_two.json()["revisions"]
        assert len(revs) == 2
        assert revs[0]["stage"] == "superseded"
        assert revs[0]["valid_from"] == "2020-01-01T00:00:00Z"
        assert revs[0]["valid_to"] == "2024-06-01T00:00:00Z"
        assert revs[1]["stage"] == "active"
        assert revs[1]["valid_from"] == "2024-06-01T00:00:00Z"
        assert revs[1]["valid_to"] == ""

        # 4. Forced permanent projection failure -> stage == "failed" with safe plain-English error.
        async def fail_sql_projection(*_args, **_kwargs):
            raise projection_worker.PermanentProjectionError(
                "internal-id-mesa-chunk-deadbeef traceback simulated"
            )

        monkeypatch.setattr(
            projection_worker, "_try_apply_sql_batch", fail_sql_projection
        )
        monkeypatch.setattr(
            projection_worker, "_apply_with_lease_heartbeat", fail_sql_projection
        )
        ingest_fail = await owner.post(
            "/v4/documents",
            json={
                "tenant_id": "t1",
                "workspace_id": "ws1",
                "dataset_id": "ds1",
                "document_id": "doc-fail",
                "title": "Failing Doc",
                "source_ref": "file://fail.txt",
                "content": "Chunk destined to fail projection.",
            },
        )
        assert ingest_fail.status_code == 202
        await _drain_work(dao)

        st_failed = await reader.get(
            "/v4/documents/doc-fail",
            params={"tenant_id": "t1", "workspace_id": "ws1", "dataset_id": "ds1"},
        )
        assert st_failed.status_code == 200, st_failed.text
        rev_failed = st_failed.json()["revisions"][0]
        assert rev_failed["stage"] == "failed"
        err = rev_failed["error"]
        assert err is not None
        assert err["code"] == "PermanentProjectionError"
        assert err["message"] == FAILURE_CLASS_MESSAGES["PermanentProjectionError"]
        assert "mesa-chunk" not in err["message"]
        assert "Traceback" not in err["message"]
        assert "simulated" not in err["message"]

        # 5. Auth and 404 checks.
        assert (
            await outsider.get(
                "/v4/documents/doc-lifecycle",
                params={"tenant_id": "t1", "workspace_id": "ws1", "dataset_id": "ds1"},
            )
        ).status_code == 403
        assert (
            await reader.get(
                "/v4/documents/missing-doc",
                params={"tenant_id": "t1", "workspace_id": "ws1", "dataset_id": "ds1"},
            )
        ).status_code == 404

        # 6. Async SDK get_document.
        async_sdk = AsyncMesaV4Client(base_url="http://test", api_key=reader_key)
        async_sdk._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://test",
            headers={"X-API-Key": reader_key},
        )
        try:
            doc_info = await async_sdk.get_document(
                tenant_id="t1",
                workspace_id="ws1",
                dataset_id="ds1",
                document_id="doc-lifecycle",
            )
            assert doc_info["document_id"] == "doc-lifecycle"
            assert len(doc_info["revisions"]) == 2
        finally:
            await async_sdk.aclose()


def test_sync_sdk_get_document_request() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        return httpx.Response(
            200,
            json={
                "document_id": "doc-1",
                "tenant_id": "t1",
                "dataset_id": "ds1",
                "title": "Doc 1",
                "status": "ACTIVE",
                "external_ref": None,
                "created_at": "2026-11-24T00:00:00",
                "revisions": [],
            },
        )

    sdk = MesaV4Client(base_url="http://test", api_key="k")
    sdk._client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    try:
        resp = sdk.get_document(
            tenant_id="t1", workspace_id="ws1", dataset_id="ds1", document_id="doc-1"
        )
        assert resp["document_id"] == "doc-1"
    finally:
        sdk.close()
    assert seen[0][0] == "GET"
    assert "/v4/documents/doc-1?" in seen[0][1]
