"""Article boundaries must preserve text and durable upload plans."""

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from mesa_memory.adapter.tokenizer import count_tokens
from mesa_memory.config import MesaConfig
from mesa_memory.document_chunking import chunk_document
from mesa_storage.dao import QueueUnavailableError
from tests.test_document_ingestion import _add, _payload, _rows
from tests.test_model_optional_runtime import _consume
from tests.test_model_optional_runtime import model_runtime as model_runtime


def _legal_text():
    return "Önsöz.\n" + "".join(
        f"MADDE {i}- " + "Kiracı bedeli zamanında öder. " * 130 + "\n"
        for i in range(1, 5)
    )


@pytest.mark.parametrize("separator", ["-", "–", "—", ":"])
def test_article_heading_variants_and_long_articles(separator):
    from mesa_memory.document_chunking import chunk_legal_document

    text = (
        "Önsöz.\r\n"
        f"MADDE 12{separator} Kural.\r\n"
        f"  Madde 13 {separator} " + "Uzun hüküm. " * 400 + "\r\n"
        f"GEÇİCİ MADDE 3{separator} Geçiş.\r\n"
        f"Ek Madde 1{separator} Ek hüküm.\r\n"
    )
    chunks = chunk_legal_document(text)
    assert "".join(chunks) == text
    assert all(chunk and count_tokens(chunk, "openai") <= 512 for chunk in chunks)
    assert chunks[0] == "Önsöz.\r\n"
    assert any(c.startswith("  Madde 13") for c in chunks)
    assert any(c.startswith("GEÇİCİ MADDE 3") for c in chunks)
    assert chunks[-1].startswith("Ek Madde 1")


@pytest.mark.parametrize("text", [
    "", "Karar. " * 800,
    "MADDE 1- Kural.\nMADDE 2- Kural.\n" + "Karar. " * 800,
    "madde kapsamına giren karar.\n" * 500,
    "Aktarılan MADDE 1- Kural. MADDE 2- Kural. MADDE 3- Kural. " * 80,
])
def test_non_legislation_and_fewer_than_three_headings_keep_plain_chunks(text):
    from mesa_memory.document_chunking import chunk_legal_document

    assert chunk_legal_document(text) == chunk_document(text)


def test_article_at_start_has_no_empty_preamble_chunk():
    from mesa_memory.document_chunking import chunk_legal_document

    pieces = [f"Madde {i} – Kural.\n" for i in range(1, 4)]
    assert chunk_legal_document("".join(pieces)) == pieces


def test_decision_quoting_many_articles_keeps_plain_chunks():
    from mesa_memory.document_chunking import chunk_legal_document

    text = 'Hukuk Genel Kurulu 2023/858 E., 2023/1399 K.\n\n"İçtihat Metni"\n' + _legal_text()
    assert chunk_legal_document(text) == chunk_document(text)


def test_chunking_config(monkeypatch):
    monkeypatch.delenv("MESA_CHUNKING", raising=False)
    assert MesaConfig(_env_file=None).chunking == "auto"
    monkeypatch.setenv("MESA_CHUNKING", "plain")
    assert MesaConfig(_env_file=None).chunking == "plain"
    monkeypatch.setenv("MESA_CHUNKING", "unknown")
    with pytest.raises(ValidationError):
        MesaConfig(_env_file=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("initial,resumed", [("plain", "auto"), ("auto", "plain")])
async def test_pending_revision_keeps_admitted_plan_after_restart(
    model_runtime, monkeypatch, initial, resumed
):
    from mesa_memory.document_chunking import chunk_legal_document

    text = _legal_text()
    expected = chunk_document(text) if initial == "plain" else chunk_legal_document(text)
    assert chunk_document(text) != chunk_legal_document(text)
    async with model_runtime(MESA_CHUNKING=initial) as env:
        original = type(env.dao).admit_v4_memory
        calls = 0

        async def interrupt(self, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise QueueUnavailableError("injected interruption")
            return await original(self, **kwargs)

        monkeypatch.setattr(type(env.dao), "admit_v4_memory", interrupt)
        failed = await env.client.post("/v4/documents", json=_payload(env, text))
        assert failed.status_code == 503, failed.text
        first = await _rows(env, "memory_mutations")
        assert len(first) == 1
        assert first[0]["content_payload"] == expected[0]
        await _consume(env, 3)
        monkeypatch.setattr(type(env.dao), "admit_v4_memory", original)
        await env.restart(MESA_CHUNKING=resumed)
        result = await _add(env, text)
        stored = sorted(await _rows(env, "source_chunks"), key=lambda r: r["ordinal"])
        assert [c["content_payload"] for c in stored] == expected
        assert result["chunk_count"] == len(expected)
        assert result["mutation_ids"][0] == first[0]["mutation_id"]
        await _consume(env, len(expected) + 2)
        assert (await _rows(env, "document_revisions"))[0]["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_unknown_admitted_plan_fails_without_appending(model_runtime, monkeypatch):
    async with model_runtime() as env:
        text = _legal_text()
        original = type(env.dao).admit_v4_memory
        calls = 0

        async def interrupt(self, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise QueueUnavailableError("injected interruption")
            return await original(self, **kwargs)

        monkeypatch.setattr(type(env.dao), "admit_v4_memory", interrupt)
        failed = await env.client.post("/v4/documents", json=_payload(env, text))
        assert failed.status_code == 503
        async with env.dao._sql.transaction() as db:
            await db.execute("UPDATE memory_mutations SET content_payload = 'unknown plan'")
            await db.commit()
        before = await _rows(env, "memory_mutations")
        monkeypatch.setattr(type(env.dao), "admit_v4_memory", original)
        response = await env.client.post("/v4/documents", json=_payload(env, text))
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            "admitted chunks do not match a supported chunking plan. "
            "Retry with the MESA_CHUNKING value used when this revision was "
            "first added, or add the content under a new document_id."
        )
        assert await _rows(env, "memory_mutations") == before


@pytest.mark.asyncio
async def test_ambiguous_admitted_prefix_never_silently_changes_plan(model_runtime, monkeypatch):
    from mesa_memory.document_chunking import chunk_legal_document

    # A blank-line preamble can be identical even when the later plans differ.
    text = _legal_text().replace("Önsöz.\n", "Önsöz.\n\n", 1)
    assert chunk_document(text)[0] == chunk_legal_document(text)[0]
    assert chunk_document(text) != chunk_legal_document(text)
    async with model_runtime(MESA_CHUNKING="plain") as env:
        original = type(env.dao).admit_v4_memory
        calls = 0

        async def interrupt(self, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise QueueUnavailableError("injected interruption")
            return await original(self, **kwargs)

        monkeypatch.setattr(type(env.dao), "admit_v4_memory", interrupt)
        response = await env.client.post("/v4/documents", json=_payload(env, text))
        assert response.status_code == 503
        before = await _rows(env, "memory_mutations")
        monkeypatch.setattr(type(env.dao), "admit_v4_memory", original)
        await env.restart(MESA_CHUNKING="auto")
        response = await env.client.post("/v4/documents", json=_payload(env, text))
        assert response.status_code == 409
        assert response.json()["detail"] == (
            "admitted chunking plan is ambiguous; no chunks appended. "
            "Retry with the MESA_CHUNKING value used when this revision was "
            "first added, or add the content under a new document_id."
        )
        assert await _rows(env, "memory_mutations") == before


@pytest.mark.skipif(not os.environ.get("MESA_BENCH_ROOT"), reason="frozen corpus is external")
def test_frozen_tier_m_article_boundaries_and_lossless_slices():
    from mesa_memory.document_chunking import chunk_legal_document

    root = Path(os.environ["MESA_BENCH_ROOT"])
    documents = [
        d for line in (root / "documents.jsonl").read_text().splitlines()
        if "M" in (d := json.loads(line))["tiers"]
    ]
    starts = {}
    for document in documents:
        text = (root / "corpus" / document["file"]).read_text()
        chunks = chunk_legal_document(text)
        assert "".join(chunks) == text, document["file"]
        assert all(c and count_tokens(c, "openai") <= 512 for c in chunks)
        if document["type"] not in {"legislation", "regulation", "communique"}:
            assert chunks == chunk_document(text), document["file"]
        offset = 0
        starts[document["file"]] = set()
        for chunk in chunks:
            starts[document["file"]].add(offset)
            offset += len(chunk)
    articles = [
        a for line in (root / "articles.jsonl").read_text().splitlines()
        if (a := json.loads(line))["file"] in starts
    ]
    matched = sum(a["char_start"] in starts[a["file"]] for a in articles)
    assert matched / len(articles) >= 0.95
