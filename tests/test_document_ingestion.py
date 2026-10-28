"""D29 document contract through the real combined runtime."""

import asyncio
import hashlib
import importlib.util
import socket

import pytest

from mesa_memory.adapter.tokenizer import count_tokens
from mesa_storage.dao import QueueUnavailableError
from tests.test_model_optional_runtime import _consume
from tests.test_model_optional_runtime import model_runtime as model_runtime


def _payload(env, content, **changes):
    return {
        "session_id": env.session_id,
        "dataset_id": "main",
        "document_id": "whole-document",
        "title": "Durable memory",
        "source_ref": "file://whole.txt",
        "content": content,
        **changes,
    }


async def _add(env, content, **changes):
    response = await env.client.post(
        "/v4/documents", json=_payload(env, content, **changes)
    )
    assert response.status_code == 202, response.text
    return response.json()


async def _rows(env, table):
    async with env.dao._sql.connection() as db:
        async with db.execute(f"SELECT * FROM {table}") as cursor:
            return [dict(row) for row in await cursor.fetchall()]


@pytest.mark.asyncio
async def test_document_chunks_commit_search_and_duplicate(model_runtime):
    content = "\n\n".join(["MESA preserves durable memory. " * 75] * 10)
    assert len(content.split()) == 3000
    async with model_runtime() as env:
        result = await _add(env, content, metadata={"department": "legal"})
        revision = (
            "rev-"
            + hashlib.sha256(
                (
                    "whole-document\n" + hashlib.sha256(content.encode()).hexdigest()
                ).encode()
            ).hexdigest()[:32]
        )
        assert result["revision_id"] == revision
        assert result["revision_number"] == 1 and result["duplicate"] is False
        assert result["chunk_count"] == len(result["mutation_ids"]) > 1
        chunks = sorted(await _rows(env, "source_chunks"), key=lambda r: r["ordinal"])
        assert "".join(c["content_payload"] for c in chunks) == content
        assert all(count_tokens(c["content_payload"], "openai") <= 512 for c in chunks)
        for ordinal, chunk in enumerate(chunks):
            async with env.dao._sql.connection() as db:
                assert (
                    await env.dao._catalog.external_id_in_tx(
                        db,
                        tenant_id="model-tenant",
                        kind="chunk",
                        physical_id=chunk["chunk_id"],
                    )
                    == f"{revision}-c{ordinal:05d}"
                )
        await _consume(env, result["chunk_count"] + 2)
        assert {r["state"] for r in await _rows(env, "memory_mutations")} == {
            "COMMITTED"
        }
        search = await env.client.post(
            "/v4/memory/search",
            json={
                "session_id": env.session_id,
                "query": "durable memory",
                "limit": 50,
            },
        )
        assert search.status_code == 200, search.text
        assert {
            a["mutation_id"] for r in search.json()["results"] for a in r["provenance"]
        } == set(result["mutation_ids"])
        before = {
            t: await _rows(env, t)
            for t in (
                "memory_mutations",
                "source_chunks",
                "document_revisions",
                "raw_logs",
                "projection_outbox",
            )
        }
        # Content identity wins over later presentation metadata and sessions.
        duplicate = await _add(env, content, title="Another title", metadata={})
        assert duplicate == {**result, "duplicate": True}
        assert before == {t: await _rows(env, t) for t in before}
        assert env.provider.embeddings == env.provider.completions == 0


@pytest.mark.asyncio
async def test_changed_document_supersedes_active_revision(model_runtime):
    async with model_runtime() as env:
        first = await _add(env, "Durable memory has the old rule.")
        await _consume(env, 3)
        second = await _add(env, "Durable memory has the new rule.")
        assert second["revision_number"] == 2
        await _consume(env, 3)
        revisions = await env.dao.list_v4_revisions(
            tenant_id="model-tenant", dataset_id="main", document_id="whole-document"
        )
        assert [r["status"] for r in revisions] == ["SUPERSEDED", "ACTIVE"]
        assert revisions[1]["supersedes_revision_id"] == first["revision_id"]
        search = await env.client.post(
            "/v4/memory/search",
            json={
                "session_id": env.session_id,
                "query": "durable memory",
            },
        )
        assert {
            a["revision_id"] for r in search.json()["results"] for a in r["provenance"]
        } == {second["revision_id"]}
        assert await _add(env, "Durable memory has the old rule.") == {
            **first,
            "duplicate": True,
        }


@pytest.mark.asyncio
async def test_document_retry_resumes_after_partial_admission(
    model_runtime, monkeypatch
):
    async with model_runtime() as env:
        original = type(env.dao).admit_v4_memory
        calls = 0

        async def interrupt(self, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise QueueUnavailableError("injected interruption")
            return await original(self, **kwargs)

        monkeypatch.setattr(type(env.dao), "admit_v4_memory", interrupt)
        content = "Durable memory. " * 600
        failed = await env.client.post("/v4/documents", json=_payload(env, content))
        assert failed.status_code == 503, failed.text
        assert len(await _rows(env, "memory_mutations")) == 1
        await _consume(env, 3)  # Incomplete revision must not activate yet.
        assert (await _rows(env, "document_revisions"))[0]["status"] == "PENDING"
        monkeypatch.setattr(type(env.dao), "admit_v4_memory", original)
        await env.restart()
        result = await _add(env, content)
        assert result["revision_number"] == 1
        assert result["duplicate"] is False
        assert len(await _rows(env, "memory_mutations")) == result["chunk_count"]
        assert len(set(result["mutation_ids"])) == result["chunk_count"]
        await _consume(env, result["chunk_count"] + 2)
        assert (await _rows(env, "document_revisions"))[0]["status"] == "ACTIVE"
        assert await _add(env, content) == {**result, "duplicate": True}


@pytest.mark.asyncio
async def test_purged_document_is_refused(model_runtime):
    async with model_runtime() as env:
        await _add(env, "Durable memory.")
        await _consume(env, 3)
        await env.dao.purge_v4_document(
            tenant_id="model-tenant", dataset_id="main", document_id="whole-document"
        )
        response = await env.client.post(
            "/v4/documents", json=_payload(env, "Durable memory.")
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "document is purged"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,status",
    [("", 422), (" " * 12, 422), ("a" * 5_000_001, 413), ("ü" * 2_500_001, 413)],
    ids=["empty", "blank", "oversize-ascii", "oversize-utf8"],
)
async def test_document_content_errors(model_runtime, content, status):
    async with model_runtime() as env:
        response = await env.client.post("/v4/documents", json=_payload(env, content))
        assert response.status_code == status, response.text[:200]
        assert await _rows(env, "memory_mutations") == []
        assert await _rows(env, "documents") == []


@pytest.mark.asyncio
async def test_document_secrets_match_insert_validation(model_runtime):
    async with model_runtime() as env:
        content = "Durable memory. api_key=private-value"
        document = await env.client.post("/v4/documents", json=_payload(env, content))
        insert = await env.client.post(
            "/v4/memory/insert",
            json={
                **_payload(env, content),
                "revision_id": "secret-rev",
                "chunk_id": "secret-chunk",
            },
        )
        assert document.status_code == insert.status_code == 422
        assert document.json()["detail"] == insert.json()["detail"]
        assert await _rows(env, "documents") == []


@pytest.mark.asyncio
async def test_document_requires_session_scope_and_admission(model_runtime):
    async with model_runtime() as env:
        response = await env.client.post(
            "/v4/documents", json=_payload(env, "Durable memory.", dataset_id="outside")
        )
        assert response.status_code == 403, response.text
        env.dao._canonical_v4_writes_enabled = False
        response = await env.client.post(
            "/v4/documents", json=_payload(env, "Durable memory.")
        )
        assert response.status_code == 503, response.text
        assert await _rows(env, "documents") == []


@pytest.mark.parametrize(
    "content",
    [
        "Durable memory. " * 1000,
        "Tek paragraf " * 2000,
        "無空白🙂" * 1500,
        "First paragraph.\n\n" * 200,
        "\t Leading whitespace.\r\n\r\n" + "Last sentence! " * 600 + "\n  ",
    ],
    ids=["sentences", "paragraph", "unicode-hard-cuts", "paragraphs", "whitespace"],
)
def test_chunking_keeps_exact_slices_with_token_bound(content):
    assert importlib.util.find_spec("mesa_memory.document_chunking") is not None
    from mesa_memory.document_chunking import chunk_document

    chunks = chunk_document(content)
    assert len(chunks) > 1
    assert "".join(chunks) == content
    assert all(chunk and count_tokens(chunk, "openai") <= 512 for chunk in chunks)


def test_chunking_prefers_paragraphs_then_sentences():
    assert importlib.util.find_spec("mesa_memory.document_chunking") is not None
    from mesa_memory.document_chunking import chunk_document

    paragraph = "First sentence. " * 100 + "\n\n"
    assert chunk_document(paragraph + "Second paragraph. " * 100)[0] == paragraph
    sentence = "word " * 300 + ". "
    assert chunk_document(sentence + "end " * 300)[0] == sentence


@pytest.mark.asyncio
async def test_flash_sdk_against_real_document_endpoint(
    model_runtime, monkeypatch, tmp_path
):
    import uvicorn

    from mesa_client import AsyncMesaV4Client, Mesa, MesaV4Client
    from mesa_memory.api import server

    async with model_runtime() as env:
        await server.state.access_control.grant_scope_role(
            "model-principal",
            tenant_id="model-tenant",
            workspace_id="default",
            dataset_id="",
            role="OWNER",
        )
        await server.state.access_control.grant_dataset_permission(
            "model-principal",
            tenant_id="model-tenant",
            dataset_id="main",
            permission="PURGE",
        )
        for name, value in {
            "MESA_HOME": str(tmp_path / "sdk-home"),
            "MESA_TENANT_ID": "model-tenant",
            "MESA_AGENT_ID": "model-agent",
        }.items():
            monkeypatch.setenv(name, value)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            base_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
            web = uvicorn.Server(
                uvicorn.Config(server.app, lifespan="off", log_level="critical")
            )
            task = asyncio.create_task(web.serve(sockets=[sock]))
            try:
                async with asyncio.timeout(10):
                    while not web.started:
                        if task.done():
                            await task
                        await asyncio.sleep(0.01)
                content = "Kira bedeli her ayın 15'inci günü ödenir."
                kwargs = {"base_url": base_url, "api_key": "model-test-key"}

                def sync_add():
                    with MesaV4Client(**kwargs) as client:
                        return client.add_document(
                            **_payload(env, content, document_id="sdk-sync")
                        )

                result = await asyncio.to_thread(sync_add)
                assert (
                    result["document_id"] == "sdk-sync" and result["chunk_count"] == 1
                )
                async with AsyncMesaV4Client(**kwargs) as client:
                    result = await client.add_document(
                        **_payload(env, content, document_id="sdk-async")
                    )
                    assert (
                        result["document_id"] == "sdk-async"
                        and result["revision_number"] == 1
                    )
                with Mesa(**kwargs, dataset_id="main") as mesa:
                    first = await asyncio.to_thread(
                        mesa.add, content, source="file://kira.txt"
                    )
                    assert (
                        first["document_id"]
                        == "doc-" + hashlib.sha256(b"file://kira.txt").hexdigest()[:32]
                    )
                    session_id = mesa._session_id
                    assert (
                        await asyncio.to_thread(
                            mesa.add, content, source="file://kira.txt"
                        )
                    )["duplicate"]
                    await _consume(env, 6)
                    assert await asyncio.to_thread(mesa.search, "kira")
                    assert "<UNTRUSTED_MEMORY_EVIDENCE>" in await asyncio.to_thread(
                        mesa.context, "kira"
                    )
                    assert mesa._session_id == session_id
                    assert (await asyncio.to_thread(mesa.delete, first["document_id"]))[
                        "state"
                    ] == "PURGED"
            finally:
                web.should_exit = True
                await task


def test_chunking_never_treats_a_period_inside_a_number_as_a_sentence_end():
    from mesa_memory.document_chunking import chunk_document

    text = " ".join(
        f"Madde {n}- (1) Kiracı, kira bedelini TBK m. 314 ve 6098 sayılı Kanun "
        f"md. {300 + n} uyarınca her ayın beşinci gününe kadar öder. (2) Yarg. 3. HD, "
        f"12.03.2019 tarih ve 2018/1234 E. sayılı kararında (bkz. s. {40 + n}) bu "
        "süreyi kesin kabul etmiştir. (3) Aşağıdaki hâllerde kiraya veren ihtar "
        "gönderir: 1. Kira bedelinin ödenmemesi, 2. Yan giderlerin ödenmemesi, "
        "3. Taşınmazın özenle kullanılmaması. (4) Bu madde hükümleri, Av. Dr. Ahmet "
        f"Yılmaz'ın görüşü dikkate alınarak md. {n}/2 ile birlikte uygulanır."
        for n in range(1, 13)
    )
    chunks = chunk_document(text)
    assert "".join(chunks) == text
    for chunk, following in zip(chunks, chunks[1:]):
        # A cut right after punctuation is a sentence end only when whitespace
        # follows it in the text; "12.|03.2019" is not.
        assert not (chunk[-1] in ".!?" and not following[0].isspace()), (
            chunk[-30:],
            following[:30],
        )


def test_chunking_never_cuts_after_abbreviation_or_list_number():
    import re

    from mesa_memory.document_chunking import chunk_document

    abbrevs = {
        "md",
        "mad",
        "bkz",
        "vb",
        "vs",
        "sy",
        "sf",
        "no",
        "yarg",
        "hd",
        "cgk",
        "hgk",
        "av",
        "dr",
        "prof",
        "doç",
        "yrd",
        "ltd",
        "şti",
    }
    text = " ".join(
        f"Madde {n}- (1) Kiracı, kira bedelini TBK m. 314 ve 6098 sayılı Kanun "
        f"md. {300 + n} uyarınca her ayın beşinci gününe kadar öder. (2) Yarg. 3. HD, "
        f"12.03.2019 tarih ve 2018/1234 E. sayılı kararında (bkz. s. {40 + n}) bu "
        "süreyi kesin kabul etmiştir. (3) Aşağıdaki hâllerde kiraya veren ihtar "
        "gönderir: 1. Kira bedelinin ödenmemesi, 2. Yan giderlerin ödenmemesi, "
        "3. Taşınmazın özenle kullanılmaması. (4) Bu madde hükümleri, Av. Dr. Ahmet "
        f"Yılmaz'ın görüşü dikkate alınarak md. {n}/2 ile birlikte uygulanır."
        for n in range(1, 13)
    )
    chunks = chunk_document(text)
    assert "".join(chunks) == text
    assert len(chunks) > 1
    for chunk in chunks[:-1]:
        stripped = chunk.rstrip()
        assert stripped.endswith((".", "!", "?")), stripped[-30:]
        if stripped.endswith("."):
            before = stripped[:-1]
            m = re.search(r"([^\W_]+)$", before)
            if m:
                w = m.group(1)
                assert not w.isdigit(), f"Cut after digits: {stripped[-30:]}"
                assert not (len(w) == 1 and w.isalpha()), f"Cut after single letter: {stripped[-30:]}"
                norm = w.replace("İ", "i").replace("I", "ı").lower()
                assert norm not in abbrevs, f"Cut after abbreviation: {stripped[-30:]}"


def test_chunking_falls_back_to_unfiltered_sentence_ends_when_none_remain():
    from mesa_memory.document_chunking import chunk_document

    # Repeated text where every period is preceded by an abbreviation or list number.
    unit = "Bu fıkra hükmü genel kurul kararı uyarınca uygulanır bkz. "
    text = unit * 80
    chunks = chunk_document(text)
    assert "".join(chunks) == text
    assert len(chunks) > 1
    for chunk in chunks[:-1]:
        # Must cut at one of the sentence ends rather than mid-word hard cut
        assert chunk.endswith("bkz. "), f"Expected cut at sentence end, got {chunk[-30:]!r}"

