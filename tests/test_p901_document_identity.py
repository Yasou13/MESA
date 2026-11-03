"""Generic law identities must come from the selected lossless article plan."""

import json

import pytest

from mesa_memory.config import MesaConfig
from mesa_storage.dao import QueueUnavailableError
from mesa_storage.legal_identity import (
    LegalEntityResolver,
    canonicalize_trusted_legal_identity,
)
from mesa_workers.projection_worker import _triplets
from tests.test_document_ingestion import _add, _payload, _rows
from tests.test_model_optional_runtime import _consume
from tests.test_model_optional_runtime import model_runtime as model_runtime


@pytest.mark.parametrize(
    "number,code", [("7201", "7201"), ("6098", "TBK"), ("7596", "7596")]
)
def test_numeric_law_identity(number, code):
    identity = canonicalize_trusted_legal_identity(
        {"law_id": number, "article_id": "21"}
    )
    assert identity["law_id"] == number
    assert identity["statute_code"] == code
    for bad in ("UNKNOWN", "12", "123456", "7201x"):
        with pytest.raises(ValueError):
            canonicalize_trusted_legal_identity({"law_id": bad, "article_id": "21"})
    with pytest.raises(ValueError):
        canonicalize_trusted_legal_identity(
            {"law_id": number, "statute_code": "TCK", "article_id": "21"}
        )


@pytest.mark.parametrize(
    "query,expected",
    [
        ("7201 sayılı Kanunun 21. maddesi", [("7201", "21")]),
        ("6098 sayılı Kanunun 49 uncu maddesi", [("TBK", "49")]),
        ("TBK m. 49", [("TBK", "49")]),
        ("5237 sayılı Kanun m. 81", [("TCK", "81")]),
        ("7201 sayılı Tebligat Kanunu'nun 21. maddesi", [("7201", "21")]),
        ("MADDE 19 uyarınca işlem yapılır", []),
    ],
)
def test_generic_numbered_query(query, expected):
    assert [
        (c.statute_code, c.article)
        for c in LegalEntityResolver().extract_citations(query)
    ] == expected


def _text(header="Kanun Numarası: 7201"):
    return (
        header
        + "\nÖnsöz.\n"
        + (
            "MADDE 21- " + "Tebligat usulü uygulanır. " * 160 + "\n"
            "MADDE 22- Başka hüküm.\n"
            "GEÇİCİ MADDE 1- Geçiş hükmü.\n"
            "EK MADDE 1- Ek hüküm.\n"
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flags,header,metadata,number",
    [
        ({}, "Kanun Numarası: 7201", {}, "7201"),
        ({}, "Kanunun Numarası: 4734", {}, "4734"),
        ({}, "Kanun\nNo. 7596", {}, "7596"),
        ({}, "Başlık", {"law_number": "6098"}, "6098"),
        ({"MESA_CHUNKING": "plain"}, "Kanun Numarası: 7201", {}, None),
        ({"MESA_LEGAL_RULES_ENABLED": False}, "Kanun Numarası: 7201", {}, None),
    ],
)
async def test_document_plan_identity(model_runtime, flags, header, metadata, number):
    async with model_runtime(**flags) as env:
        result = await _add(env, _text(header), metadata=metadata)
        by_id = {m["mutation_id"]: m for m in await _rows(env, "memory_mutations")}
        mutations = [by_id[mid] for mid in result["mutation_ids"]]
        current = None
        counts = {}
        for mutation in mutations:
            text = mutation["content_payload"]
            if text.startswith("MADDE "):
                current = text.split()[1].rstrip("-")
            elif text.startswith((header, "GEÇİCİ", "EK")):
                current = None
            identity = json.loads(mutation["metadata_json"]).get("_mesa_legal_identity")
            if number and current:
                assert identity["law_id"] == number
                assert identity["article_id"] == current
                counts[current] = counts.get(current, 0) + 1
            else:
                assert identity is None
        if number:
            assert counts["21"] > 1
        await _consume(env, result["chunk_count"] + 2)
        for assertion in await _rows(env, "v4_assertions"):
            mutation = next(
                m for m in mutations if m["mutation_id"] == assertion["mutation_id"]
            )
            identity = json.loads(mutation["metadata_json"]).get("_mesa_legal_identity")
            projected = await env.dao.get_projection_mutation(mutation["mutation_id"])
            fallback = _triplets(projected)[0]
            assert (fallback["head"], fallback["relation"], fallback["tail"]) == (
                mutation["document_id"], "madde", mutation["chunk_id"]
            )
            assert fallback["literal_value"] is None
            assert assertion["predicate"] == "madde"
            assert assertion["object_entity_id"]
            assert (assertion["law_id"] or "") == (
                identity["law_id"] if identity else ""
            )


def test_rules_config(monkeypatch):
    monkeypatch.delenv("MESA_LEGAL_RULES_ENABLED", raising=False)
    assert MesaConfig(_env_file=None).legal_rules_enabled is True
    monkeypatch.setenv("MESA_LEGAL_RULES_ENABLED", "false")
    assert MesaConfig(_env_file=None).legal_rules_enabled is False


@pytest.mark.asyncio
async def test_resume_does_not_rewrite_admitted_identity(model_runtime, monkeypatch):
    async with model_runtime() as env:
        original = type(env.dao).admit_v4_memory
        calls = 0

        async def interrupt(self, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise QueueUnavailableError("injected interruption")
            return await original(self, **kwargs)

        monkeypatch.setattr(type(env.dao), "admit_v4_memory", interrupt)
        response = await env.client.post("/v4/documents", json=_payload(env, _text()))
        assert response.status_code == 503
        before = await _rows(env, "memory_mutations")
        assert all(json.loads(m["metadata_json"])["_mesa_document_chunk"] is True for m in before)
        assert any(
            json.loads(m["metadata_json"]).get("_mesa_legal_identity") for m in before
        )
        monkeypatch.setattr(type(env.dao), "admit_v4_memory", original)
        await env.restart(MESA_CHUNKING="plain", MESA_LEGAL_RULES_ENABLED=False)
        await _add(env, _text())
        after = {m["mutation_id"]: m for m in await _rows(env, "memory_mutations")}
        assert all(after[m["mutation_id"]]["metadata_json"] == m["metadata_json"] for m in before)


def test_catalog_memory_fallback_keeps_article_subject():
    record = {
        "document_id": "catalog-law",
        "chunk_id": "catalog-article",
        "content_payload": "MADDE 49- Tazminat hükmü.",
        "legal_identity": canonicalize_trusted_legal_identity({
            "law_id": "6098", "article_id": "49", "identity_source": "catalog"
        }),
    }
    triplet = _triplets(record)[0]
    assert (triplet["head"], triplet["relation"], triplet["tail"]) == (
        "TBK m.49", "hükmü", None
    )
    assert triplet["literal_value"] == record["content_payload"]


@pytest.mark.parametrize("source", ["catalog", "verified_ingestion"])
@pytest.mark.parametrize("document_chunk", [False, True])
def test_fallback_origin_is_independent_of_identity_source(source, document_chunk):
    record = {
        "document_id": "document",
        "chunk_id": "chunk",
        "content_payload": "MADDE 49- Tazminat hükmü.",
        "document_chunk": document_chunk,
        "legal_identity": canonicalize_trusted_legal_identity({
            "law_id": "6098", "article_id": "49", "identity_source": source
        }),
    }
    triplet = _triplets(record)[0]
    if document_chunk:
        assert (triplet["head"], triplet["relation"], triplet["tail"]) == (
            "document", "madde", "chunk"
        )
        assert triplet["literal_value"] is None
    else:
        assert (triplet["head"], triplet["relation"], triplet["tail"]) == (
            "TBK m.49", "hükmü", None
        )
        assert triplet["literal_value"] == record["content_payload"]


@pytest.mark.asyncio
async def test_document_origin_marker_is_internal_and_durable(model_runtime):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        added = await _add(
            env,
            "Kanun Numarası: 6098\nMADDE 49- Tazminat hükmü.\n"
            "MADDE 50- Başka hüküm.\nMADDE 51- Son hüküm.\n",
        )
        content = "MADDE 49- Tazminat hükmü."
        response = await env.client.post(
            "/v4/memory/insert?document_chunk=true&_document_chunk=true",
            json={
                "session_id": env.session_id,
                "dataset_id": "main",
                "document_id": "explicit-memory",
                "revision_id": "explicit-revision",
                "chunk_id": "explicit-chunk",
                "title": "Explicit article",
                "source_ref": "file://explicit",
                "content": content,
                "legal_identity": {"law_id": "6098", "article_id": "49"},
                "metadata": {"_mesa_document_chunk": True},
                "document_chunk": True,
            },
        )
        assert response.status_code == 422
        payload = json.loads(response.request.content)
        payload["metadata"] = {}
        response = await env.client.post(
            "/v4/memory/insert?document_chunk=true&_document_chunk=true", json=payload
        )
        assert response.status_code == 202, response.text
        manual_mid = response.json()["mutation_id"]
        mutations = {m["mutation_id"]: m for m in await _rows(env, "memory_mutations")}
        for mid in added["mutation_ids"]:
            assert json.loads(mutations[mid]["metadata_json"])["_mesa_document_chunk"] is True
        assert "_mesa_document_chunk" not in json.loads(mutations[manual_mid]["metadata_json"])
        raw_mutations = {m["raw_log_id"]: m["mutation_id"] for m in mutations.values()}
        for raw in await _rows(env, "raw_logs"):
            payload = json.loads(raw["payload"])
            assert payload["metadata"].get("_mesa_document_chunk", False) is (
                raw_mutations[raw["id"]] in added["mutation_ids"]
            )
        await _consume(env, added["chunk_count"] + 3)
        article_mid = next(
            mid for mid in added["mutation_ids"]
            if (json.loads(mutations[mid]["metadata_json"]).get("_mesa_legal_identity") or {}).get("article_id") == "49"
        )
        doc = await env.dao.get_projection_mutation(article_mid)
        manual = await env.dao.get_projection_mutation(manual_mid)
        assert doc["document_chunk"] is True
        assert manual["document_chunk"] is False
        assert doc["legal_identity"] == manual["legal_identity"]
        assert "_mesa_document_chunk" not in doc["metadata"]
        doc_fallback = _triplets(doc)[0]
        assert (doc_fallback["head"], doc_fallback["relation"], doc_fallback["tail"]) == (
            doc["document_id"], "madde", doc["chunk_id"]
        )
        manual_fallback = _triplets(manual)[0]
        assert (manual_fallback["head"], manual_fallback["relation"], manual_fallback["tail"]) == (
            "TBK m.49", "hükmü", None
        )
        assertions = {a["mutation_id"]: a for a in await _rows(env, "v4_assertions")}
        assert assertions[article_mid]["predicate"] == "madde"
        assert assertions[article_mid]["literal_value"] is None
        assert assertions[manual_mid]["predicate"] == "hükmü"
        assert assertions[manual_mid]["literal_value"] == content
        from mesa_memory.api import server

        operation = server.app.openapi()["paths"]["/v4/memory/insert"]["post"]
        assert operation["operationId"] == "insert_memory_v4_memory_insert_post"
        assert not any(p["name"] in {"document_chunk", "_document_chunk"} for p in operation.get("parameters", []))
