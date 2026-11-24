"""P17-02 tests: out-of-order dated revisions, history insertion, FTS/vector isolation, and crash recovery (D87)."""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from mesa_memory.api import server
from mesa_memory.config import configured_embedding_identity
from mesa_memory.embedding.service import EmbeddingService, _deterministic_mock_vector
from mesa_storage.dao import QueueUnavailableError, _dataset_fts_table_name


@pytest.fixture
def versioned_runtime(tmp_path, monkeypatch):
    """Real combined runtime supporting restart and optional deterministic vectors."""

    @asynccontextmanager
    async def runtime(*, storage_name: str, embedding_enabled: bool = False):
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
            "MESA_EMBEDDING_ENABLED": "true" if embedding_enabled else "false",
            "MESA_EXTRACTION_ENABLED": "false",
            "MESA_CHUNKING": "auto",
            "MESA_API_KEY": "r24-p1702-key",
            "MESA_PRINCIPAL_ID": "r24-p1702-principal",
            "MESA_PRINCIPAL_STATUS": "active",
        }
        if embedding_enabled:
            env_vars["MESA_EMBEDDING_DIMENSION"] = "64"
            monkeypatch.setattr(
                server,
                "_get_embedding_service",
                lambda **_kw: EmbeddingService(
                    identity=configured_embedding_identity(),
                    provider_fn=lambda text: _deterministic_mock_vector(text, 64),
                    allow_model_loading=False,
                    external_enabled=False,
                ),
            )
        for key, value in env_vars.items():
            monkeypatch.setenv(key, value)

        async def hold_polling(*args, **kwargs):
            import asyncio

            await asyncio.Event().wait()

        monkeypatch.setattr(server, "_run_combined_durable_consumer", hold_polling)

        async with AsyncExitStack() as stack:
            await stack.enter_async_context(server.lifespan(server.app))
            dao = server.state.dao
            await dao.ensure_v4_catalog_scope(
                tenant_id="law-tenant", workspace_id="default", dataset_id="laws"
            )
            access = server.state.access_control
            await access.grant_principal_permission(
                "r24-p1702-principal", "law-agent", "SESSION_CREATE"
            )
            await access.grant_scope_role(
                "r24-p1702-principal",
                tenant_id="law-tenant",
                workspace_id="default",
                dataset_id="laws",
                role="WRITER",
            )
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=server.app),
                    base_url="http://test",
                    headers={"X-API-Key": "r24-p1702-key"},
                )
            )
            started = await client.post(
                "/v4/sessions/start",
                json={
                    "tenant_id": "law-tenant",
                    "workspace_id": "default",
                    "dataset_ids": ["laws"],
                    "agent_id": "law-agent",
                },
            )
            assert started.status_code == 201, started.text
            env = SimpleNamespace(
                dao=dao,
                client=client,
                session_id=started.json()["session_id"],
            )

            async def restart():
                await stack.aclose()
                await stack.enter_async_context(server.lifespan(server.app))
                env.dao = server.state.dao
                env.client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=server.app),
                        base_url="http://test",
                        headers={"X-API-Key": "r24-p1702-key"},
                    )
                )

            env.restart = restart
            yield env

    return runtime


async def _drain(env, max_steps: int = 40) -> None:
    for _ in range(max_steps):
        work = await server._consume_combined_durable_work_once(
            env.dao,
            fact_extraction_service=server.state.fact_extraction_service,
            model_processing_enabled=server.state.fact_extraction_service is not None,
        )
        if not work:
            break


def _law_version(rate_text: str, extra_clause: str) -> str:
    return (
        "VERGİ USUL DENEME KANUNU\n"
        "Kanun Numarası: 213\n\n"
        f"MADDE 1 - Gecikme faizi oranı {rate_text} olarak uygulanır.\n\n"
        f"MADDE 2 - Bildirim süresi {extra_clause}.\n\n"
        "MADDE 3 - Yürürlük hükümleri bu kanuna tabidir.\n"
    )


async def _snapshot_revisions(env) -> dict[str, dict[str, str]]:
    """Return per-revision status, valid_from, valid_to keyed by external revision_id."""
    async with env.dao._sql.connection() as db:
        async with db.execute(
            "SELECT ci.external_id, r.status, "
            "COALESCE(MIN(a.valid_from), ''), COALESCE(MAX(a.valid_to), ''), "
            "COALESCE(MIN(a.status), ''), COALESCE(MAX(a.status), ''), "
            "r.supersedes_revision_id "
            "FROM document_revisions r "
            "JOIN v4_catalog_identities ci "
            "ON ci.kind = 'revision' AND ci.physical_id = r.revision_id "
            "LEFT JOIN v4_assertions a ON a.revision_id = r.revision_id "
            "GROUP BY r.revision_id"
        ) as cur:
            rows = await cur.fetchall()
    result: dict[str, dict[str, str]] = {}
    for rev_id, rev_status, vf, vt, a_min, a_max, sup in rows:
        assert a_min == a_max == rev_status, (rev_id, rev_status, a_min, a_max)
        result[str(rev_id)] = {
            "status": str(rev_status),
            "valid_from": str(vf),
            "valid_to": str(vt),
            "has_supersedes": "yes" if sup else "no",
        }
    return result


async def _persistent_fts_revisions(env) -> set[str]:
    """Return external revision_ids currently present in the persistent dataset FTS5 index."""
    async with env.dao._sql.connection() as db:
        p_ds = await env.dao._catalog.resolve_id_in_tx(
            db, tenant_id="law-tenant", kind="dataset", external_id="laws"
        )
        fts_table = _dataset_fts_table_name("law-tenant", p_ds)
        exists = await env.dao._dataset_fts_exists_in_tx(db, fts_table)
        if not exists:
            return set()
        async with db.execute(
            f"SELECT DISTINCT ci.external_id FROM main.{fts_table} f "
            "JOIN v4_assertions a ON a.rowid = f.rowid "
            "JOIN v4_catalog_identities ci "
            "ON ci.kind = 'revision' AND ci.physical_id = a.revision_id"
        ) as cur:
            return {str(r[0]) for r in await cur.fetchall()}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "embedding_enabled", [False, True], ids=["models_off", "deterministic_vectors"]
)
async def test_three_versions_loaded_in_any_order_produce_identical_history_and_search(
    versioned_runtime, embedding_enabled: bool
) -> None:
    versions = {
        1: (
            "2020-01-01T00:00:00Z",
            _law_version("yüzde on", "on beş gündür"),
        ),
        2: (
            "2022-01-01T00:00:00Z",
            _law_version("yüzde yirmi", "otuz gündür"),
        ),
        3: (
            "2024-01-01T00:00:00Z",
            _law_version("yüzde kırk", "altmış gündür"),
        ),
    }

    async def _run_order(order: tuple[int, int, int], label: str):
        async with versioned_runtime(
            storage_name=f"order_{label}_{'vec' if embedding_enabled else 'bm25'}",
            embedding_enabled=embedding_enabled,
        ) as env:
            rev_ids: dict[int, str] = {}
            for idx in order:
                vf, text = versions[idx]
                resp = await env.client.post(
                    "/v4/documents",
                    json={
                        "session_id": env.session_id,
                        "dataset_id": "laws",
                        "document_id": "vuk-213",
                        "title": "Vergi Usul Kanunu",
                        "source_ref": f"mevzuat://213/v{idx}",
                        "content": text,
                        "metadata": {"valid_from": vf},
                    },
                )
                assert resp.status_code == 202, resp.text
                rev_ids[idx] = resp.json()["revision_id"]
                await _drain(env)
                # Trigger an undated search to ensure persistent FTS is built and checked
                undated_mid = await env.client.post(
                    "/v4/memory/search",
                    json={
                        "session_id": env.session_id,
                        "query": "gecikme faizi oranı",
                        "limit": 10,
                    },
                )
                assert undated_mid.status_code == 200
                # Persistent FTS must contain ONLY the currently highest loaded version
                highest_so_far = max(rev_ids)
                assert await _persistent_fts_revisions(env) == {rev_ids[highest_so_far]}

            snap = await _snapshot_revisions(env)
            # In order (3, 1, 2), history revisions 1 and 2 have supersedes_revision_id=None
            if order == (3, 1, 2):
                assert snap[rev_ids[1]]["has_supersedes"] == "no"
                assert snap[rev_ids[2]]["has_supersedes"] == "no"

            # Run undated and dated searches across every interval & boundary
            search_points = [
                ("undated", {}),
                ("before_v1", {"valid_at": "2019-12-31T23:59:59Z"}),
                ("on_v1", {"valid_at": "2020-01-01T00:00:00Z"}),
                ("mid_v1", {"valid_at": "2021-06-01T00:00:00Z"}),
                ("on_v2", {"valid_at": "2022-01-01T00:00:00Z"}),
                ("mid_v2", {"valid_at": "2023-06-01T00:00:00Z"}),
                ("on_v3", {"valid_at": "2024-01-01T00:00:00Z"}),
                ("after_v3", {"valid_at": "2025-06-01T00:00:00Z"}),
            ]
            search_outputs: dict[str, list[tuple[str, str, str]]] = {}
            for name, temporal in search_points:
                res = await env.client.post(
                    "/v4/memory/search",
                    json={
                        "session_id": env.session_id,
                        "query": "gecikme faizi oranı",
                        "limit": 10,
                        **temporal,
                    },
                )
                assert res.status_code == 200, res.text
                body = res.json()
                if embedding_enabled and name != "before_v1":
                    assert body["vector_lane"]["status"] == "active"
                    assert any(
                        "vector" in item["retrieval_provenance"]["origins"]
                        for item in body["results"]
                    )
                search_outputs[name] = [
                    (
                        item["provenance"][0]["revision_id"],
                        item["source_chunk_id"],
                        item["evidence_span"],
                    )
                    for item in body["results"]
                ]

            return (
                rev_ids,
                {
                    rid: {k: v for k, v in info.items() if k != "has_supersedes"}
                    for rid, info in snap.items()
                },
                search_outputs,
            )

    rev_ids_123, snap_123, search_123 = await _run_order((1, 2, 3), "123")
    rev_ids_312, snap_312, search_312 = await _run_order((3, 1, 2), "312")
    rev_ids_213, snap_213, search_213 = await _run_order((2, 1, 3), "213")

    assert rev_ids_123 == rev_ids_312 == rev_ids_213
    expected_snap = {
        rev_ids_123[1]: {
            "status": "SUPERSEDED",
            "valid_from": "2020-01-01T00:00:00Z",
            "valid_to": "2022-01-01T00:00:00Z",
        },
        rev_ids_123[2]: {
            "status": "SUPERSEDED",
            "valid_from": "2022-01-01T00:00:00Z",
            "valid_to": "2024-01-01T00:00:00Z",
        },
        rev_ids_123[3]: {
            "status": "ACTIVE",
            "valid_from": "2024-01-01T00:00:00Z",
            "valid_to": "",
        },
    }
    assert snap_123 == expected_snap
    assert snap_312 == expected_snap
    assert snap_213 == expected_snap

    # Dated and undated search results must be identical regardless of load order
    assert search_312 == search_123
    assert search_213 == search_123

    # Verify each time point returned the exact expected version
    assert {r[0] for r in search_123["undated"]} == {rev_ids_123[3]}
    assert search_123["before_v1"] == []
    assert {r[0] for r in search_123["on_v1"]} == {rev_ids_123[1]}
    assert {r[0] for r in search_123["mid_v1"]} == {rev_ids_123[1]}
    assert {r[0] for r in search_123["on_v2"]} == {rev_ids_123[2]}
    assert {r[0] for r in search_123["mid_v2"]} == {rev_ids_123[2]}
    assert {r[0] for r in search_123["on_v3"]} == {rev_ids_123[3]}
    assert {r[0] for r in search_123["after_v3"]} == {rev_ids_123[3]}


@pytest.mark.asyncio
async def test_equal_valid_from_409_and_undated_on_dated_422_and_duplicate_409(
    versioned_runtime,
) -> None:
    async with versioned_runtime(storage_name="conflicts") as env:
        v_latest = _law_version("yüzde kırk", "altmış gündür")
        v_older = _law_version("yüzde on", "on beş gündür")
        v_other = _law_version("yüzde elli", "doksan gündür")

        r1 = await env.client.post(
            "/v4/documents",
            json={
                "session_id": env.session_id,
                "dataset_id": "laws",
                "document_id": "vuk-213",
                "title": "Vergi Usul Kanunu",
                "source_ref": "mevzuat://213/v3",
                "content": v_latest,
                "metadata": {"valid_from": "2024-01-01T00:00:00Z"},
            },
        )
        assert r1.status_code == 202
        await _drain(env)

        # Load older version as history
        r2 = await env.client.post(
            "/v4/documents",
            json={
                "session_id": env.session_id,
                "dataset_id": "laws",
                "document_id": "vuk-213",
                "title": "Vergi Usul Kanunu",
                "source_ref": "mevzuat://213/v1",
                "content": v_older,
                "metadata": {"valid_from": "2020-01-01T00:00:00Z"},
            },
        )
        assert r2.status_code == 202
        await _drain(env)

        async def _counts() -> tuple[int, int]:
            async with env.dao._sql.connection() as db:
                async with db.execute("SELECT COUNT(*) FROM document_revisions") as c1:
                    revs = int((await c1.fetchone())[0])
                async with db.execute("SELECT COUNT(*) FROM memory_mutations") as c2:
                    muts = int((await c2.fetchone())[0])
            return revs, muts

        before_counts = await _counts()

        # 1. Equal valid_from to ACTIVE revision (same UTC instant via +03:00) -> 409
        eq_active = await env.client.post(
            "/v4/documents",
            json={
                "session_id": env.session_id,
                "dataset_id": "laws",
                "document_id": "vuk-213",
                "title": "Vergi Usul Kanunu",
                "source_ref": "mevzuat://213/conflict1",
                "content": v_other,
                "metadata": {"valid_from": "2024-01-01T03:00:00+03:00"},
            },
        )
        assert eq_active.status_code == 409, eq_active.text
        assert await _counts() == before_counts

        # 2. Equal valid_from to SUPERSEDED history revision -> 409
        eq_hist = await env.client.post(
            "/v4/documents",
            json={
                "session_id": env.session_id,
                "dataset_id": "laws",
                "document_id": "vuk-213",
                "title": "Vergi Usul Kanunu",
                "source_ref": "mevzuat://213/conflict2",
                "content": v_other,
                "metadata": {"valid_from": "2020-01-01T00:00:00Z"},
            },
        )
        assert eq_hist.status_code == 409, eq_hist.text
        assert await _counts() == before_counts

        # 3. Undated revision on a dated document -> 422
        undated_err = await env.client.post(
            "/v4/documents",
            json={
                "session_id": env.session_id,
                "dataset_id": "laws",
                "document_id": "vuk-213",
                "title": "Vergi Usul Kanunu",
                "source_ref": "mevzuat://213/undated",
                "content": v_other,
            },
        )
        assert undated_err.status_code == 422, undated_err.text
        assert await _counts() == before_counts

        # 4. Re-uploading identical content of the SUPERSEDED history revision -> 409 unchanged
        dup_hist = await env.client.post(
            "/v4/documents",
            json={
                "session_id": env.session_id,
                "dataset_id": "laws",
                "document_id": "vuk-213",
                "title": "Vergi Usul Kanunu",
                "source_ref": "mevzuat://213/v1-again",
                "content": v_older,
                "metadata": {"valid_from": "2025-01-01T00:00:00Z"},
            },
        )
        assert dup_hist.status_code == 409, dup_hist.text
        assert "restoring an earlier revision is not supported" in dup_hist.text
        assert await _counts() == before_counts


@pytest.mark.asyncio
async def test_undated_documents_behave_unchanged(versioned_runtime) -> None:
    async with versioned_runtime(storage_name="undated_unchanged") as env:
        r1 = await env.client.post(
            "/v4/documents",
            json={
                "session_id": env.session_id,
                "dataset_id": "laws",
                "document_id": "plain-doc",
                "title": "Plain Doc",
                "source_ref": "file://plain-v1",
                "content": "İlk sürüm metni burada yer alır.",
            },
        )
        assert r1.status_code == 202
        rev1_id = r1.json()["revision_id"]
        await _drain(env)

        r2 = await env.client.post(
            "/v4/documents",
            json={
                "session_id": env.session_id,
                "dataset_id": "laws",
                "document_id": "plain-doc",
                "title": "Plain Doc",
                "source_ref": "file://plain-v2",
                "content": "İkinci sürüm metni burada yer alır.",
            },
        )
        assert r2.status_code == 202
        rev2_id = r2.json()["revision_id"]
        await _drain(env)

        snap = await _snapshot_revisions(env)
        assert snap[rev1_id]["status"] == "SUPERSEDED"
        assert snap[rev1_id]["valid_from"] == ""
        assert snap[rev1_id]["valid_to"] == ""
        assert snap[rev2_id]["status"] == "ACTIVE"
        assert snap[rev2_id]["valid_from"] == ""
        assert snap[rev2_id]["valid_to"] == ""
        assert snap[rev2_id]["has_supersedes"] == "yes"


@pytest.mark.asyncio
async def test_crash_between_chunk_commit_and_finalize_for_history_revision_is_retryable(
    versioned_runtime, monkeypatch
) -> None:
    """P16-10 style interruption during multi-chunk history upload: safe before retry and clean after."""
    long_history = (
        "VERGİ USUL DENEME KANUNU\n"
        "Kanun Numarası: 213\n\n"
        + "\n\n".join(
            f"MADDE {n} - Eski tarihli madde {n} hükmü gereğince vergi oranı binde {n} uygulanır. "
            + ("Bu düzenleme geçmiş dönem işlemleri için geçerlidir. " * 25)
            for n in range(1, 5)
        )
    )
    current_text = _law_version("yüzde kırk", "altmış gündür")

    async with versioned_runtime(storage_name="history_crash_recovery") as env:
        # 1. Load current (2024) version first -> ACTIVE
        r_curr = await env.client.post(
            "/v4/documents",
            json={
                "session_id": env.session_id,
                "dataset_id": "laws",
                "document_id": "vuk-crash",
                "title": "Vergi Usul Kanunu",
                "source_ref": "mevzuat://213/v2024",
                "content": current_text,
                "metadata": {"valid_from": "2024-01-01T00:00:00Z"},
            },
        )
        assert r_curr.status_code == 202
        curr_rev_id = r_curr.json()["revision_id"]
        await _drain(env)
        # Ensure persistent FTS exists for the dataset
        res_init = await env.client.post(
            "/v4/memory/search",
            json={"session_id": env.session_id, "query": "gecikme faizi", "limit": 10},
        )
        assert res_init.status_code == 200
        assert await _persistent_fts_revisions(env) == {curr_rev_id}

        # 2. Inject crash on the 4th chunk of the older (2020) history revision
        original_admit = type(env.dao).admit_v4_memory
        calls = 0

        async def interrupt_fourth_chunk(self, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 4:
                raise QueueUnavailableError("injected crash before finalize")
            return await original_admit(self, **kwargs)

        monkeypatch.setattr(type(env.dao), "admit_v4_memory", interrupt_fourth_chunk)
        hist_payload = {
            "session_id": env.session_id,
            "dataset_id": "laws",
            "document_id": "vuk-crash",
            "title": "Vergi Usul Kanunu",
            "source_ref": "mevzuat://213/v2020",
            "content": long_history,
            "metadata": {"valid_from": "2020-01-01T00:00:00Z"},
        }
        interrupted = await env.client.post("/v4/documents", json=hist_payload)
        assert interrupted.status_code == 503

        # 3. Even if chunks 0..2 commit BEFORE retry, history revision remains PENDING,
        # never enters persistent FTS, and never leaks into undated or dated search.
        monkeypatch.setattr(type(env.dao), "admit_v4_memory", original_admit)
        await _drain(env)
        assert await _persistent_fts_revisions(env) == {curr_rev_id}

        undated_mid = await env.client.post(
            "/v4/memory/search",
            json={
                "session_id": env.session_id,
                "query": "geçmiş dönem işlemleri",
                "limit": 10,
            },
        )
        assert undated_mid.status_code == 200
        assert undated_mid.json()["results"] == []

        dated_mid = await env.client.post(
            "/v4/memory/search",
            json={
                "session_id": env.session_id,
                "query": "geçmiş dönem işlemleri",
                "limit": 10,
                "valid_at": "2021-06-01T00:00:00Z",
            },
        )
        assert dated_mid.status_code == 200
        assert dated_mid.json()["results"] == []

        # 4. Restart and retry the exact same upload -> completes and finalizes as SUPERSEDED
        await env.restart()
        retried = await env.client.post("/v4/documents", json=hist_payload)
        assert retried.status_code == 202, retried.text
        hist_rev_id = retried.json()["revision_id"]
        assert retried.json()["chunk_count"] >= 4
        await _drain(env)

        snap = await _snapshot_revisions(env)
        assert snap[curr_rev_id]["status"] == "ACTIVE"
        assert snap[curr_rev_id]["valid_from"] == "2024-01-01T00:00:00Z"
        assert snap[curr_rev_id]["valid_to"] == ""
        assert snap[hist_rev_id]["status"] == "SUPERSEDED"
        assert snap[hist_rev_id]["valid_from"] == "2020-01-01T00:00:00Z"
        assert snap[hist_rev_id]["valid_to"] == "2024-01-01T00:00:00Z"

        # Persistent FTS STILL contains only current revision
        assert await _persistent_fts_revisions(env) == {curr_rev_id}

        # Dated search in 2021 now finds the completed history revision; undated still returns only current
        dated_done = await env.client.post(
            "/v4/memory/search",
            json={
                "session_id": env.session_id,
                "query": "geçmiş dönem işlemleri",
                "limit": 10,
                "valid_at": "2021-06-01T00:00:00Z",
            },
        )
        assert dated_done.status_code == 200
        assert {
            item["provenance"][0]["revision_id"]
            for item in dated_done.json()["results"]
        } == {hist_rev_id}

        undated_done = await env.client.post(
            "/v4/memory/search",
            json={
                "session_id": env.session_id,
                "query": "geçmiş dönem işlemleri",
                "limit": 10,
            },
        )
        assert undated_done.status_code == 200
        assert undated_done.json()["results"] == []
