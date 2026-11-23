"""P14-02 (D63): 5-character prefix BM25 for Turkish in the per-query temporary FTS5 index."""

from __future__ import annotations

import aiosqlite
import pytest

from tests.test_v4_bm25_scope_isolation import _environment
from tests.test_v4_retrieval_hardening_phase1 import _create_committed_mutation


def _trace_fts(monkeypatch):
    statements: list[tuple[str, object]] = []
    original_execute = aiosqlite.Connection.execute

    def traced(self, sql, parameters=None):
        statements.append((sql, parameters))
        return original_execute(self, sql, parameters)

    monkeypatch.setattr(aiosqlite.Connection, "execute", traced)
    return statements


@pytest.mark.asyncio
async def test_fts_unicode61_splits_ascii_and_typographic_apostrophes(tmp_path):
    """D63: verify unicode61 already splits ASCII (') and typographic (’) apostrophes."""
    sql, _vector, _graph, dao = await _environment(tmp_path)
    try:
        c1 = await _create_committed_mutation(
            dao,
            raw_log_id=1,
            tenant_id="t",
            agent_id="a",
            dataset_id="d",
            document_id="doc-1",
            chunk_id="chunk-ascii",
            content="Yargıtay'ın bozma hükmü uygulanır.",
            subject="Yargıtay",
            predicate="hukum",
            literal_value="Yargıtay'ın bozma hükmü uygulanır.",
            evidence_span="Yargıtay'ın bozma hükmü uygulanır.",
        )
        c2 = await _create_committed_mutation(
            dao,
            raw_log_id=2,
            tenant_id="t",
            agent_id="a",
            dataset_id="d",
            document_id="doc-2",
            chunk_id="chunk-typo",
            content="Yargıtay\u2019ın onama hükmü kesinleşir.",
            subject="Yargıtay",
            predicate="hukum",
            literal_value="Yargıtay\u2019ın onama hükmü kesinleşir.",
            evidence_span="Yargıtay\u2019ın onama hükmü kesinleşir.",
        )

        # Both apostrophe forms split so an exact-stem lookup finds both chunks.
        for query in ("Yargıtay", "Yargıtay'ın", "Yargıtay\u2019ın"):
            results = await dao.search_v4_memory(
                tenant_id="t",
                agent_id="a",
                dataset_ids=["d"],
                query=query,
            )
            found = {r["assertion_id"] for r in results}
            assert {c1["assertion_id"], c2["assertion_id"]}.issubset(found)
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_fts_prefix5_matches_other_suffixes_and_uses_prefix5_option(
    tmp_path, monkeypatch
):
    """A 6+ character token without digits searches its first 5 chars with prefix='5'."""
    sql, _vector, _graph, dao = await _environment(tmp_path)
    statements = _trace_fts(monkeypatch)
    try:
        row = await _create_committed_mutation(
            dao,
            raw_log_id=1,
            tenant_id="t",
            agent_id="a",
            dataset_id="d",
            document_id="doc-1",
            chunk_id="chunk-1",
            content="Sözleşmenin feshi halinde tazminat ödenir.",
            subject="Belge",
            predicate="kural",
            literal_value="Sözleşmenin feshi halinde tazminat ödenir.",
            evidence_span="Sözleşmenin feshi halinde tazminat ödenir.",
        )

        results = await dao.search_v4_memory(
            tenant_id="t",
            agent_id="a",
            dataset_ids=["d"],
            query="sözleşmeden tazminatı",
        )
        assert results
        assert results[0]["assertion_id"] == row["assertion_id"]

        create_stmts = [
            s for s, _ in statements if "CREATE VIRTUAL TABLE temp." in s and "fts5(" in s
        ]
        assert create_stmts
        assert any("prefix='5'" in s for s in create_stmts)

        match_exprs = [
            params[0]
            for s, params in statements
            if "MATCH ?" in s and "bm25(" in s and isinstance(params, tuple)
        ]
        assert match_exprs
        assert '"sözle"*' in match_exprs[-1]
        assert '"tazmi"*' in match_exprs[-1]
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_fts_prefix5_keeps_turkish_dotless_i_variant(tmp_path):
    """Prefix-5 tokens retain the ı→i variant so uppercase ASCII-I text still matches."""
    sql, _vector, _graph, dao = await _environment(tmp_path)
    try:
        row = await _create_committed_mutation(
            dao,
            raw_log_id=1,
            tenant_id="t",
            agent_id="a",
            dataset_id="d",
            document_id="doc-1",
            chunk_id="chunk-1",
            content="YARGITAYIN BOZMA KARARI",
            subject="Belge",
            predicate="kural",
            literal_value="YARGITAYIN BOZMA KARARI",
            evidence_span="YARGITAYIN BOZMA KARARI",
        )

        results = await dao.search_v4_memory(
            tenant_id="t",
            agent_id="a",
            dataset_ids=["d"],
            query="yargıtaydan",
        )
        assert results
        assert results[0]["assertion_id"] == row["assertion_id"]
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_fts_token_with_digit_stays_exact(tmp_path, monkeypatch):
    """A 6+ character token containing any digit must stay exact (no prefix wildcard)."""
    sql, _vector, _graph, dao = await _environment(tmp_path)
    statements = _trace_fts(monkeypatch)
    try:
        row = await _create_committed_mutation(
            dao,
            raw_log_id=1,
            tenant_id="t",
            agent_id="a",
            dataset_id="d",
            document_id="doc-1",
            chunk_id="chunk-1",
            content="Referans kodu kod123456 olarak kaydedildi.",
            subject="Belge",
            predicate="kural",
            literal_value="Referans kodu kod123456 olarak kaydedildi.",
            evidence_span="Referans kodu kod123456 olarak kaydedildi.",
        )

        # Shares 5-char prefix "kod12" with "kod123456" and has length >= 6,
        # but contains digits so it must stay exact and not match "kod123456".
        miss = await dao.search_v4_memory(
            tenant_id="t",
            agent_id="a",
            dataset_ids=["d"],
            query="kod129999",
        )
        assert miss == []

        match_exprs = [
            params[0]
            for s, params in statements
            if "MATCH ?" in s and "bm25(" in s and isinstance(params, tuple)
        ]
        assert match_exprs
        assert match_exprs[-1] == '"kod129999"'

        hit = await dao.search_v4_memory(
            tenant_id="t",
            agent_id="a",
            dataset_ids=["d"],
            query="kod123456",
        )
        assert hit
        assert hit[0]["assertion_id"] == row["assertion_id"]
    finally:
        await sql.close()


@pytest.mark.asyncio
async def test_fts_short_token_under_6_chars_stays_exact(tmp_path, monkeypatch):
    """Tokens shorter than 6 characters stay exact and do not prefix-match longer words."""
    sql, _vector, _graph, dao = await _environment(tmp_path)
    statements = _trace_fts(monkeypatch)
    try:
        await _create_committed_mutation(
            dao,
            raw_log_id=1,
            tenant_id="t",
            agent_id="a",
            dataset_id="d",
            document_id="doc-1",
            chunk_id="chunk-longer",
            content="Mahkeme kararları kesinleşti.",
            subject="Belge",
            predicate="kural",
            literal_value="Mahkeme kararları kesinleşti.",
            evidence_span="Mahkeme kararları kesinleşti.",
        )

        # "karar" has length 5 (< 6): must stay exact and not match "kararları".
        miss = await dao.search_v4_memory(
            tenant_id="t",
            agent_id="a",
            dataset_ids=["d"],
            query="karar",
        )
        assert miss == []

        match_exprs = [
            params[0]
            for s, params in statements
            if "MATCH ?" in s and "bm25(" in s and isinstance(params, tuple)
        ]
        assert match_exprs
        assert match_exprs[-1] == '"karar"'

        exact_row = await _create_committed_mutation(
            dao,
            raw_log_id=2,
            tenant_id="t",
            agent_id="a",
            dataset_id="d",
            document_id="doc-2",
            chunk_id="chunk-exact",
            content="Mahkeme karar verdi.",
            subject="Belge",
            predicate="kural",
            literal_value="Mahkeme karar verdi.",
            evidence_span="Mahkeme karar verdi.",
        )
        hit = await dao.search_v4_memory(
            tenant_id="t",
            agent_id="a",
            dataset_ids=["d"],
            query="karar",
        )
        assert [r["assertion_id"] for r in hit] == [exact_row["assertion_id"]]
    finally:
        await sql.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "foreign_scope",
    [
        ("tenant-b", "agent-b", "dataset-b"),
        ("tenant-a", "agent-a", "dataset-b"),
        ("tenant-a", "agent-b", "dataset-a"),
    ],
)
async def test_fts_prefix5_out_of_scope_rows_cannot_change_rank(tmp_path, foreign_scope):
    """Invariant 3: out-of-scope rows with the same 5-char prefix cannot change rank or scores."""
    sql, _vector, _graph, dao = await _environment(tmp_path)
    try:
        a1 = await _create_committed_mutation(
            dao,
            raw_log_id=1,
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_id="dataset-a",
            document_id="doc-a1",
            chunk_id="chunk-a1",
            content="Sözleşmenin feshi halinde tazminat ödenir ve sorumluluk doğar.",
            subject="Belge A1",
            predicate="kural",
            literal_value="Sözleşmenin feshi halinde tazminat ödenir.",
            evidence_span="Sözleşmenin feshi halinde tazminat ödenir ve sorumluluk doğar.",
        )
        await _create_committed_mutation(
            dao,
            raw_log_id=2,
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_id="dataset-a",
            document_id="doc-a2",
            chunk_id="chunk-a2",
            content="Sözleşmeler yazılı şekle tabidir.",
            subject="Belge A2",
            predicate="kural",
            literal_value="Sözleşmeler yazılı şekle tabidir.",
            evidence_span="Sözleşmeler yazılı şekle tabidir.",
        )

        query = "sözleşmeden tazminatı"
        before = await dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query=query,
        )
        assert len(before) == 2
        bm25_ranks = {
            r["assertion_id"]: r["retrieval_provenance"]["lane_ranks"]["bm25"]
            for r in before
        }
        assert bm25_ranks[a1["assertion_id"]] == 1

        for idx in range(10, 50):
            await _create_committed_mutation(
                dao,
                raw_log_id=idx,
                tenant_id=foreign_scope[0],
                agent_id=foreign_scope[1],
                dataset_id=foreign_scope[2],
                document_id=f"doc-foreign-{idx}",
                chunk_id=f"chunk-foreign-{idx}",
                content=f"Sözleşmeler sözleşmenin sözleşmeden tazminat tazminatı #{idx}.",
                subject=f"Foreign {idx}",
                predicate="kural",
                literal_value="Sözleşmeler tazminat",
                evidence_span=f"Sözleşmeler tazminat #{idx}.",
            )

        after = await dao.search_v4_memory(
            tenant_id="tenant-a",
            agent_id="agent-a",
            dataset_ids=["dataset-a"],
            query=query,
        )
        assert [r["assertion_id"] for r in after] == [r["assertion_id"] for r in before]
        assert [r["final_score"] for r in after] == [r["final_score"] for r in before]
        assert [
            r["retrieval_provenance"]["raw_scores"] for r in after
        ] == [r["retrieval_provenance"]["raw_scores"] for r in before]
    finally:
        await sql.close()
