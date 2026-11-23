"""P16-01 (D76): internal ids must never participate in lexical or assertion scoring."""

from __future__ import annotations

import re

import pytest

from mesa_memory.consolidation.schemas import MemoryCandidate
from mesa_workers.projection_worker import _triplets
from tests.test_v4_bm25_scope_isolation import _environment


async def _commit_assertion(
    dao,
    *,
    raw_log_id: int,
    tenant_id: str = "t",
    agent_id: str = "a",
    dataset_id: str = "d",
    document_id: str,
    revision_id: str = "rev-1",
    chunk_id: str,
    source_ref: str,
    content: str,
    subject: str,
    predicate: str = "madde",
    object_value: str | None = None,
    literal_value: str | None = None,
    evidence_span: str | None = None,
) -> dict:
    cand = MemoryCandidate.from_raw_log(
        raw_log_id=raw_log_id,
        tenant_id=tenant_id,
        workspace_id="w",
        dataset_id=dataset_id,
        document_id=document_id,
        revision_id=revision_id,
        chunk_id=chunk_id,
        source_ref=source_ref,
        agent_id=agent_id,
        session_id="sess-1",
        content_payload=content,
        embedding_provider="test",
        embedding_model="catalog-contract",
        embedding_version="v1",
        embedding_dimension=2,
        embedding_space_id="test:catalog-contract:v1:2:norm=true",
        embedding_normalized=True,
    ).as_consolidation_record()
    await dao.record_mutation(cand, raw_log_id=raw_log_id)
    mut = await dao.get_projection_mutation(str(cand["mutation_id"]))
    assert mut is not None

    await dao.project_v4_sql_entity(mutation=mut, entity_name=subject)
    if object_value:
        await dao.project_v4_sql_entity(mutation=mut, entity_name=object_value)

    triplet: dict[str, object] = {
        "head": subject,
        "relation": predicate,
        "evidence_span": evidence_span if evidence_span is not None else content,
    }
    if object_value:
        triplet["tail"] = object_value
    if literal_value is not None:
        triplet["literal_value"] = literal_value

    await dao.project_v4_graph_triplet(mutation=mut, triplet=triplet)
    assertions = await dao.list_v4_assertions_for_mutation(str(mut["mutation_id"]))
    assert len(assertions) >= 1
    assertion = assertions[0]

    async with dao._sql.transaction() as db:
        await db.execute(
            "UPDATE memory_mutations SET state = 'COMMITTED' WHERE mutation_id = ?",
            (mut["mutation_id"],),
        )
        await db.commit()
    return assertion


@pytest.mark.asyncio
async def test_internal_document_and_chunk_ids_do_not_lift_lexical_or_assertion_score(
    tmp_path,
):
    """A query number present only inside an internal id must not lift that document."""
    sql, _vector, _graph, dao = await _environment(tmp_path)
    try:
        # doc-alpha sorts before doc-beta on equal scores (D70 tie-break).
        # Give doc-beta an internal document_id and chunk_id containing 4721 and 8392.
        doc_a = await _commit_assertion(
            dao,
            raw_log_id=1,
            document_id="doc-alpha",
            chunk_id="chunk-alpha-1",
            source_ref="file://ortak_kira.txt",
            content="Kira sözleşmesinin feshi ve tahliye şartları.",
            subject="mesa-document-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            predicate="madde",
            object_value="mesa-chunk-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        )
        doc_b = await _commit_assertion(
            dao,
            raw_log_id=2,
            document_id="doc-beta",
            chunk_id="chunk-beta-1",
            source_ref="file://ortak_kira.txt",
            content="Kira sözleşmesinin feshi ve tahliye şartları.",
            subject="mesa-document-cccccccc4721cccccccccccccccccccc",
            predicate="madde",
            object_value="mesa-chunk-dddddddd8392dddddddddddddddddddd",
        )

        for query in ("kira 4721", "kira 8392"):
            bm25_results = await dao.search_v4_memory(
                tenant_id="t",
                agent_id="a",
                dataset_ids=["d"],
                query=query,
                graph_enabled=False,
                rrf_weights={"bm25": 1.0, "assertion": 0.0, "vector": 0.0, "graph": 0.0},
            )
            assert [r["assertion_id"] for r in bm25_results[:2]] == [
                doc_a["assertion_id"],
                doc_b["assertion_id"],
            ]
            assert bm25_results[0]["retrieval_provenance"]["raw_scores"]["bm25"] == (
                bm25_results[1]["retrieval_provenance"]["raw_scores"]["bm25"]
            )

            assertion_results = await dao.search_v4_memory(
                tenant_id="t",
                agent_id="a",
                dataset_ids=["d"],
                query=query,
                graph_enabled=False,
                rrf_weights={"bm25": 0.0, "assertion": 1.0, "vector": 0.0, "graph": 0.0},
            )
            assert [r["assertion_id"] for r in assertion_results[:2]] == [
                doc_a["assertion_id"],
                doc_b["assertion_id"],
            ]
            assert (
                assertion_results[0]["retrieval_provenance"]["raw_scores"]["assertion"]
                == assertion_results[1]["retrieval_provenance"]["raw_scores"][
                    "assertion"
                ]
            )
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_catalog_mapped_physical_id_digits_do_not_lift_document(tmp_path):
    """End-to-end check with real catalog-mapped physical IDs (subject == document_id)."""
    sql, _vector, _graph, dao = await _environment(tmp_path)
    try:
        await dao._catalog.ensure_scope(tenant_id="t", workspace_id="w", dataset_id="d")
        phys_b = ""
        ext_b = ""
        for idx in range(1, 200):
            candidate_ext = f"doc-zzz-{idx:04d}"
            async with sql.transaction() as db:
                phys = await dao._catalog.resolve_id_in_tx(
                    db,
                    tenant_id="t",
                    kind="document",
                    external_id=candidate_ext,
                    create=True,
                )
            match = re.search(r"\d{4}", phys)
            if match:
                phys_b = phys
                ext_b = candidate_ext
                secret_digits = match.group(0)
                break
        assert phys_b and ext_b

        ext_a = ""
        for idx in range(1, 200):
            candidate_ext = f"doc-aaa-{idx:04d}"
            async with sql.transaction() as db:
                phys = await dao._catalog.resolve_id_in_tx(
                    db,
                    tenant_id="t",
                    kind="document",
                    external_id=candidate_ext,
                    create=True,
                )
            if secret_digits not in phys:
                ext_a = candidate_ext
                break
        assert ext_a

        # Project both documents using the real fallback _triplets shape (head=document_id, tail=chunk_id).
        assertions_by_ext: dict[str, dict] = {}
        for raw_id, ext_doc, ext_chunk in (
            (10, ext_a, "chunk-a-0"),
            (11, ext_b, "chunk-b-0"),
        ):
            cand = MemoryCandidate.from_raw_log(
                raw_log_id=raw_id,
                tenant_id="t",
                workspace_id="w",
                dataset_id="d",
                document_id=ext_doc,
                revision_id=f"rev-{ext_doc}",
                chunk_id=ext_chunk,
                source_ref="file://ortak.txt",
                agent_id="a",
                session_id="sess-1",
                content_payload="Tahliye taahhütnamesi geçerlilik koşulları.",
                embedding_provider="test",
                embedding_model="catalog-contract",
                embedding_version="v1",
                embedding_dimension=2,
                embedding_space_id="test:catalog-contract:v1:2:norm=true",
                embedding_normalized=True,
            ).as_consolidation_record()
            await dao.record_mutation(cand, raw_log_id=raw_id)
            mut = await dao.get_projection_mutation(str(cand["mutation_id"]))
            assert mut is not None
            for triplet in _triplets(mut):
                await dao.project_v4_sql_entity(
                    mutation=mut, entity_name=str(triplet["head"])
                )
                if triplet.get("tail"):
                    await dao.project_v4_sql_entity(
                        mutation=mut, entity_name=str(triplet["tail"])
                    )
                await dao.project_v4_graph_triplet(mutation=mut, triplet=triplet)
            async with sql.transaction() as db:
                await db.execute(
                    "UPDATE memory_mutations SET state = 'COMMITTED' WHERE mutation_id = ?",
                    (mut["mutation_id"],),
                )
                await db.commit()
            assertions_by_ext[ext_doc] = (
                await dao.list_v4_assertions_for_mutation(str(mut["mutation_id"]))
            )[0]

        async with sql.connection() as db:
            async with db.execute(
                "SELECT a.document_id, s.canonical_name FROM v4_assertions a "
                "JOIN v4_entities s ON s.entity_id = a.subject_id "
                "WHERE a.assertion_id = ?",
                (assertions_by_ext[ext_b]["assertion_id"],),
            ) as cursor:
                row = await cursor.fetchone()
        assert row is not None
        assert secret_digits in str(row[0])
        assert str(row[0]) == str(row[1])

        results = await dao.search_v4_memory(
            tenant_id="t",
            agent_id="a",
            dataset_ids=["d"],
            query=f"tahliye {secret_digits}",
            graph_enabled=False,
        )
        assert [r["assertion_id"] for r in results[:2]] == [
            assertions_by_ext[ext_a]["assertion_id"],
            assertions_by_ext[ext_b]["assertion_id"],
        ]
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_fts_does_not_match_internal_mesa_prefix_tokens(tmp_path):
    """Fallback subject/object names like mesa-document-* must not match in FTS5."""
    sql, _vector, _graph, dao = await _environment(tmp_path)
    try:
        await _commit_assertion(
            dao,
            raw_log_id=20,
            document_id="doc-plain",
            chunk_id="chunk-plain-1",
            source_ref="file://sozlesme.txt",
            content="Taraflar sözleşmeyi karşılıklı olarak imzaladı.",
            subject="mesa-document-1234567890abcdef1234567890abcdef",
            predicate="madde",
            object_value="mesa-chunk-abcdef1234567890abcdef1234567890",
        )

        for query in ("mesa", "document", "chunk"):
            results = await dao.search_v4_memory(
                tenant_id="t",
                agent_id="a",
                dataset_ids=["d"],
                query=query,
                graph_enabled=False,
            )
            assert results == [], f"query {query!r} unexpectedly matched internal id"
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_caller_visible_source_ref_and_entity_names_still_score(tmp_path):
    """Caller-visible source_ref and non-internal entity names keep scoring."""
    sql, _vector, _graph, dao = await _environment(tmp_path)
    try:
        doc_plain = await _commit_assertion(
            dao,
            raw_log_id=30,
            document_id="doc-a-plain",
            chunk_id="chunk-a-1",
            source_ref="file://genel_belge.txt",
            content="Kira sözleşmesi uyuşmazlıkları hakkında genel bilgi.",
            subject="Genel Kurul",
            predicate="hukum",
            literal_value="Kira sözleşmesi uyuşmazlıkları hakkında genel bilgi.",
        )
        doc_ref = await _commit_assertion(
            dao,
            raw_log_id=31,
            document_id="doc-z-ref",
            chunk_id="chunk-z-1",
            source_ref="file://kira_4721_ozel.txt",
            content="Kira sözleşmesi uyuşmazlıkları hakkında genel bilgi.",
            subject="Özel Komisyon 9911",
            predicate="hukum",
            literal_value="Kira sözleşmesi uyuşmazlıkları hakkında genel bilgi.",
        )

        by_ref = await dao.search_v4_memory(
            tenant_id="t",
            agent_id="a",
            dataset_ids=["d"],
            query="kira 4721",
            graph_enabled=False,
        )
        assert by_ref[0]["assertion_id"] == doc_ref["assertion_id"]
        assert doc_plain["assertion_id"] in {r["assertion_id"] for r in by_ref}

        by_entity = await dao.search_v4_memory(
            tenant_id="t",
            agent_id="a",
            dataset_ids=["d"],
            query="kira 9911",
            graph_enabled=False,
        )
        assert by_entity[0]["assertion_id"] == doc_ref["assertion_id"]
    finally:
        await sql.close()
