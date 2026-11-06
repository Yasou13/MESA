"""One seeded harness for caller-text parsers and the R16 audit regressions."""

import asyncio
import random
import re
import signal
import threading
import time

import pytest

import mesa_storage.dao as dao_module
from mesa_mcp.adapter import _required_string
from mesa_mcp.security import reject_secrets
from mesa_mcp.v4_service import _typed_result
from mesa_memory import document_chunking as chunks
from mesa_memory.retrieval.adaptive import normalize_planner_query
from mesa_memory.security.input_validation import validate_write_payload
from mesa_memory.security.untrusted_memory import render_untrusted_memory
from mesa_storage import legal_rules
from mesa_storage.kuzu_provider import KuzuGraphProvider, _normalize_text
from mesa_storage.legal_identity import LegalEntityResolver
from tests.test_model_optional_runtime import model_runtime as model_runtime
from tests.test_p908_search_limits import _insert, _search


def _bounded_call(label, function, *args, **kwargs):
    def expired(*_):
        raise AssertionError(f"{label} exceeded 200 ms")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 0.2)
    start = time.perf_counter()
    try:
        result = function(*args, **kwargs)
        assert time.perf_counter() - start < 0.2, label
        return result
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.mark.no_cover
def test_seeded_shared_parser_harness_at_api_limits():
    rng = random.Random(1702)
    resolvers = [
        LegalEntityResolver(),
        LegalEntityResolver(law_names={"Tebligat Kanunu": "7201"}),
    ]
    atoms = [
        "a",
        "1",
        " ",
        "\n",
        "\t",
        "/",
        "TCK m. 81 ",
        "7201 sayılı Kanun ",
        "TEVDİİNE ",
        "ﷺ",
        "İ",
        "MADDE 1- ",
    ]
    texts = [
        stem * (32768 // len(stem) + 1)
        for stem in ["1", " ", "ﷺ", "TCK/1 ", "a_", "TEVDİİNE "]
    ]
    for _ in range(12):
        parts = rng.choices(atoms, k=10000)
        texts.append("".join(parts))
    for raw in texts:
        text = raw[:32768]
        query = text[:4096]
        for resolver in resolvers:
            _bounded_call("citations", resolver.extract_citations, text)
        _bounded_call("rules", legal_rules.extract, text)
        _bounded_call("DAO normalize", dao_module._normalize_identity_text, text)
        _bounded_call("graph normalize", _normalize_text, text)
        _bounded_call("planner normalize", normalize_planner_query, query)
        _bounded_call("admission validation", validate_write_payload, text, {})
        _bounded_call("MCP validation", reject_secrets, text[:20000])
        if query.strip():
            _bounded_call(
                "MCP query",
                _required_string,
                {"query": query[:2000]},
                "query",
                max_length=2000,
            )
        _bounded_call("MCP result", _typed_result, {"content": text})
        _bounded_call(
            "MCP untrusted wrapper",
            render_untrusted_memory,
            [("Memory", [{"content": text}])],
        )
        for pattern in (
            chunks._PARAGRAPH_END,
            chunks._SENTENCE_END,
            chunks._TRAILING_WORD,
        ):
            _bounded_call(
                "chunk window " + pattern.pattern, lambda: list(pattern.finditer(text))
            )
        _bounded_call(
            "graph path scoring",
            KuzuGraphProvider._compute_path_score,
            hop=3,
            seed_id="seed",
            seed_scores=None,
            query_tokens=["zz", *["z" * 7 + chr(97 + i % 26) for i in range(63)]],
            predicates=["relates"] * 3,
            evidence_spans=[text] * 3,
            jurisdictions=["TR"] * 3,
            confidences=[1.0] * 3,
            edge_directions=["forward"] * 3,
            target_statutes={"TCK"},
            competing_statutes=[],
            resolver=resolvers[0],
        )
    # Whole-document regex scans see 5 MB. Tokenization/queue throughput is
    # measured separately by P10-03; D47 defers the 5 MB secret scan.
    for stem in ["a", " ", "MADDE 1 x\n", "İçtihat Metnix\n"]:
        document = (stem * (5_000_000 // len(stem) + 1))[:5_000_000]
        for pattern in (chunks._ARTICLE_HEADING, chunks._CASE_LAW_PREAMBLE):
            _bounded_call(
                "document " + pattern.pattern, lambda: list(pattern.finditer(document))
            )


def test_forwarding_outcome_keeps_long_prefix_with_bounded_source_span():
    text = (
        " ".join(["dosyanın"] * 15)
        + " gönderilmek üzere Yargıtay Cumhuriyet Başsavcılığına TEVDİİNE"
    )
    outcomes = legal_rules.extract_outcomes(text)
    assert any(item["value"] == "gönderme" for item in outcomes)
    assert all(item["text"] == text[item["start"] : item["end"]] for item in outcomes)


def test_masked_store_name_does_not_join_acronym_to_unrelated_article():
    resolver = LegalEntityResolver(law_names={"Tebligat Kanunu": "7201"})
    citations = resolver.extract_citations("TBK Tebligat Kanunu 21.")
    assert ("TBK", "21") not in [(c.statute_code, c.article) for c in citations]
    assert ("7201", None) in [(c.statute_code, c.article) for c in citations]


def test_factored_names_keep_longest_match_and_optional_u():
    names = {f"Özel Bölge {index} Kanunu": str(7000 + index) for index in range(80)}
    names["Özel Bölge"] = "8888"
    resolver = LegalEntityResolver(law_names=names)
    for index in range(80):
        for end in ("Kanunu", "Kanun"):
            citations = resolver.extract_citations(f"Özel Bölge {index} {end} m. 49")
            assert [(c.statute_code, c.article) for c in citations] == [
                (str(7000 + index), "49")
            ]


@pytest.mark.no_cover
def test_3000_stored_names_use_word_index_without_dynamic_regex(monkeypatch):
    names = {
        (" ".join(f"w{i}x{j}" for j in range(40)))[:193] + " kanunu": "6098"
        for i in range(3000)
    }
    compile_pattern = re.compile
    fixed_patterns = {
        value.pattern
        for value in vars(LegalEntityResolver()).values()
        if isinstance(value, re.Pattern)
    }

    def checked(pattern, *args, **kwargs):
        assert pattern in fixed_patterns, "stored names must not become regex source"
        return compile_pattern(pattern, *args, **kwargs)

    monkeypatch.setattr(re, "compile", checked)
    resolver = _bounded_call(
        "3000-name construction", LegalEntityResolver, law_names=names
    )
    for index in (0, 1499, 2999):
        name = list(names)[index]
        citations = _bounded_call(
            "indexed name match", resolver.extract_citations, name + " m. 49"
        )
        assert [(c.statute_code, c.article) for c in citations] == [("TBK", "49")]


@pytest.mark.no_cover
def test_repeated_first_word_with_3000_names_stays_bounded():
    resolver = LegalEntityResolver(
        law_names={"a " * 94 + f"{i:05d} kanunu": "6098" for i in range(3000)}
    )
    assert (
        _bounded_call("repeated first word", resolver.extract_citations, "a " * 16384)
        == []
    )


def test_punctuation_prefix_keeps_longest_name():
    resolver = LegalEntityResolver(law_names={"(": "7777", "( Özel Kanunu": "8888"})
    assert [
        (c.statute_code, c.article)
        for c in resolver.extract_citations("( Özel Kanunu m. 49")
    ] == [("8888", "49")]


@pytest.mark.asyncio
async def test_normalized_store_names_keep_the_200_character_bound(model_runtime):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        ordinary = "a" * 193 + " kanunu"
        expanded = "ﷺ" * 193 + " kanunu"
        for index, name in enumerate((ordinary, expanded)):
            await _insert(
                env,
                index,
                name + " Kanun Numarası: 6098\nKural.",
                title=name,
                legal=True,
            )
        await _search(env, "TBK m. 1")
        scope = next(iter(env.dao._search_scope_cache.values()))
        assert ordinary in scope["legal_context"]["names"]
        assert all(len(name) <= 200 for name in scope["legal_context"]["names"])


@pytest.mark.asyncio
async def test_scoped_resolver_is_built_off_loop_and_reused(model_runtime, monkeypatch):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        await _insert(
            env, 0, "TBK m. 49 uygulanır.", title="Türk Borçlar Kanunu", legal=True
        )
        original = dao_module.LegalEntityResolver
        main_thread = threading.get_ident()
        calls = []

        class CheckedResolver(original):
            def __init__(self, *args, **kwargs):
                if "law_names" in kwargs:
                    calls.append(threading.get_ident())
                    assert calls[-1] != main_thread
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(dao_module, "LegalEntityResolver", CheckedResolver)
        await _search(env, "TBK m. 49")
        await _search(env, "TBK m. 1")
        assert len(calls) == 1


@pytest.mark.no_cover
@pytest.mark.asyncio
async def test_graph_scoring_yields_between_many_api_sized_edges(monkeypatch):
    graph = KuzuGraphProvider("unused")
    monkeypatch.setattr(graph, "_ensure_initialized", lambda: None)
    count = 400

    async def expand(**kwargs):
        return [
            ["seed", f"target-{i}", "name", f"a-{i}", "relates", 1.0, "TR", "forward"]
            for i in range(count)
        ]

    monkeypatch.setattr(graph, "_step_expand", expand)
    tokens = ["z" * 7 + chr(97 + i % 25) for i in range(63)]
    query = " ".join(["zz", *tokens, "TBK m. 49"])
    gaps = []

    async def heartbeat():
        last = time.perf_counter()
        while True:
            await asyncio.sleep(0.002)
            now = time.perf_counter()
            gaps.append(now - last - 0.002)
            last = now

    pulse = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.005)
    try:
        hits = await graph.search_v4_graph(
            agent_id="agent",
            seed_entity_ids=["seed"],
            allowed_entity_ids={"seed", *[f"target-{i}" for i in range(count)]},
            allowed_assertion_ids={f"a-{i}" for i in range(count)},
            assertion_evidence={f"a-{i}": "zz " + "z" * 32765 for i in range(count)},
            query=query,
            max_hops=1,
            limit=50,
        )
        await asyncio.sleep(0.005)
        assert len(hits) == 50
        assert max(gaps) < 0.2
    finally:
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)
        await graph.close()
