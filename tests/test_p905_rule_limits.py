"""The public input limits must also bound legal extraction latency."""

import asyncio
import random
import signal
import threading
import time

import pytest

from mesa_storage import legal_rules
from tests.test_document_ingestion import _add
from tests.test_model_optional_runtime import _consume
from tests.test_model_optional_runtime import model_runtime as model_runtime


def _timed_extract(text):
    def expired(*_):
        raise AssertionError("legal extraction exceeded 200 ms")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 0.2)
    start = time.perf_counter()
    try:
        items = legal_rules.extract(text)
        assert time.perf_counter() - start < 0.2
        assert all(text[it["start"] : it["end"]] == it["text"] for it in items)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.mark.parametrize(
    "stem",
    [
        " " * 1000 + "2020/1 x",
        "bir ",
        "1.000.",
        "m. 1 m. 2 ",
        " \n\t",
        "dosyanın gönderilmek üzere yerine ",
        "(Değişik ",
    ],
    ids=[
        "spaces-docket",
        "number-words",
        "dotted-digits",
        "article-list",
        "whitespace",
        "forwarding-prefix",
        "open-amendments",
    ],
)
def test_opus_repros_at_maximum_insert_size(stem):
    text = (stem * (32768 // len(stem) + 1))[:32767] + "/"
    _timed_extract(text)


def test_seeded_grammar_fuzz_at_api_limits():
    rng = random.Random(905)
    tokens = [
        " ",
        "\t",
        "\n",
        "1",
        "2020",
        "/",
        ".",
        "-",
        "E.",
        "K.",
        "Esas",
        "Karar",
        "madde",
        "sayılı",
        "bir",
        "yüz",
        "milyon",
        "TL",
        "lira",
        "dosyanın",
        "gönderilmek",
        "üzere",
        "TEVDİİNE",
    ]
    for size in (4096, 32768):
        for _ in range(24):
            parts = []
            length = 0
            while length < size:
                token = rng.choice(tokens)
                parts.append(token)
                length += len(token)
            _timed_extract("".join(parts)[:size])


def test_bounded_lists_preserve_normal_values_and_source_spans():
    text = "5gün ve yirmibeş yıl. 1.234.567,89-TL. TCK m. 17.3.2."
    items = legal_rules.extract(text)
    assert [(x["value"], x["unit"]) for x in items if x["type"] == "duration"] == [
        (5, "gün"), (25, "yıl")
    ]
    assert [x["value"] for x in items if x["type"] == "amount"] == ["1234567.89"]
    assert [x["article"] for x in items if x["type"] == "citation"] == ["17.3.2"]
    assert all(text[x["start"]:x["end"]] == x["text"] for x in items)


@pytest.mark.parametrize(
    "stem",
    [
        "ONANMASINA ",
        "01.01.2020 ",
        "TCK m. 1 ",
        "2020/1 E., 2020/2 K. ",
        "bir TL ",
        "(Değişik 01.01.2020 ",
        "geçici 1 ila geçici 999999999999 madde ",
        " " * 120 + "2020/1 E., 2020/2 K.\n",
    ],
)
def test_many_matches_and_unclosed_markers_remain_bounded(stem):
    _timed_extract((stem * (32768 // len(stem) + 1))[:32768])


def test_compiled_patterns_have_only_bounded_terminal_repetitions():
    # Guard the regex structure as well as observed runtimes. Single optional
    # clauses are allowed; repeating a group containing another repeat is not.
    import re
    from re import _constants, _parser

    def audit(tree):
        for op, args in tree:
            if op in (
                _constants.MAX_REPEAT,
                _constants.MIN_REPEAT,
                _constants.POSSESSIVE_REPEAT,
            ):
                _, maximum, body = args
                assert maximum != _constants.MAXREPEAT
                if maximum > 1:
                    assert all(
                        child
                        in (
                            _constants.LITERAL,
                            _constants.NOT_LITERAL,
                            _constants.IN,
                            _constants.ANY,
                        )
                        for child, _ in body
                    ), (op, args)
                audit(body)
            elif op is _constants.SUBPATTERN:
                audit(args[-1])
            elif op is _constants.BRANCH:
                for branch in args[1]:
                    audit(branch)
            elif op in (_constants.ASSERT, _constants.ASSERT_NOT):
                audit(args[1])

    patterns = [p for p in vars(legal_rules).values() if isinstance(p, re.Pattern)]
    patterns += [p for _, p in legal_rules._OUTCOME_RULES]
    assert len(patterns) >= 20
    for pattern in patterns:
        audit(_parser.parse(pattern.pattern, pattern.flags))


@pytest.mark.asyncio
async def test_admission_and_decision_query_extract_off_the_event_loop(
    model_runtime, monkeypatch
):
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        from mesa_storage.legal_identity import LegalEntityResolver

        main_thread = threading.get_ident()
        original = legal_rules.extract
        calls = []

        def checked(text):
            calls.append(threading.get_ident())
            assert threading.get_ident() != main_thread
            return original(text)

        monkeypatch.setattr(legal_rules, "extract", checked)
        await _add(env, "Ceza Genel Kurulu 2017/885 E., 2021/693 K.\nTBK m. 49 uygulanır.")
        await _consume(env, 3)
        admitted = len(calls)
        assert admitted
        resolver_extract = LegalEntityResolver.extract_citations
        resolved = []

        def checked_resolver(self, text):
            assert threading.get_ident() != main_thread
            resolved.append(True)
            return resolver_extract(self, text)

        monkeypatch.setattr(LegalEntityResolver, "extract_citations", checked_resolver)
        await env.dao.search_v4_memory(
            tenant_id="model-tenant",
            agent_id="model-agent",
            dataset_ids=["main"],
            query="2017/885 E., 2021/693 K.",
            limit=5,
            graph_enabled=False,
        )
        assert len(calls) > admitted
        assert resolved
        # A legal query also parses untyped passage/assertion/candidate text.
        await env.dao.search_v4_memory(
            tenant_id="model-tenant",
            agent_id="model-agent",
            dataset_ids=["main"],
            query="TBK m. 49",
            limit=5,
            graph_enabled=False,
        )
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("plan", ["auto", "plain"])
async def test_document_chunking_and_resume_alternative_run_off_loop(
    model_runtime, monkeypatch, plan
):
    from mesa_api import v4_router
    from mesa_storage.dao import QueueUnavailableError
    from tests.test_document_ingestion import _payload

    calls = []
    loop_thread = threading.get_ident()
    for name in ("chunk_legal_document", "chunk_document"):
        original = getattr(v4_router, name)

        def checked(text, name=name, original=original):
            assert threading.get_ident() != loop_thread
            calls.append(name)
            return original(text)

        monkeypatch.setattr(v4_router, name, checked)
    async with model_runtime(MESA_CHUNKING=plan, MESA_GRAPH_ENABLED=False) as env:
        original_admit = type(env.dao).admit_v4_memory
        count = 0

        async def interrupt(self, **kwargs):
            nonlocal count
            count += 1
            if count == 3:
                raise QueueUnavailableError("injected partial admission")
            return await original_admit(self, **kwargs)

        monkeypatch.setattr(type(env.dao), "admit_v4_memory", interrupt)
        content = "Durable memory preserves evidence. " * 650
        response = await env.client.post("/v4/documents", json=_payload(env, content))
        assert response.status_code == 503
        monkeypatch.setattr(type(env.dao), "admit_v4_memory", original_admit)
        await _consume(env, 4)
        calls.clear()
        await env.restart(MESA_CHUNKING="plain" if plan == "auto" else "auto")
        await _add(env, content)
        assert sorted(calls) == ["chunk_document", "chunk_legal_document"]


@pytest.mark.asyncio
async def test_whole_document_validation_runs_off_loop(model_runtime, monkeypatch):
    from mesa_api import v4_router

    original = v4_router.validate_write_payload
    loop_thread = threading.get_ident()
    large_checks = []

    def checked(content, metadata):
        if len(content) > 32768:
            assert threading.get_ident() != loop_thread
            large_checks.append(len(content))
        return original(content, metadata)

    monkeypatch.setattr(v4_router, "validate_write_payload", checked)
    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        await _add(env, " " * 32768 + "2020/1 x")
    assert large_checks == [32776]


@pytest.mark.asyncio
async def test_deferred_document_validation_keeps_rejection_contract(model_runtime):
    from tests.test_document_ingestion import _payload, _rows

    async with model_runtime(MESA_GRAPH_ENABLED=False) as env:
        cases = [
            ({"content": " "}, 422, "content must not be empty"),
            ({"content": "secret: example-placeholder"}, 422, "appears to contain a secret"),
            ({"metadata": {"_mesa_document_chunk": True}}, 422, "reserved"),
            ({"title": 123}, 422, "body.title"),
            ({"content": "x" * 5_000_001}, 413, "exceeds 5,000,000"),
        ]
        for changes, status, detail in cases:
            response = await env.client.post(
                "/v4/documents", json={**_payload(env, "Valid text."), **changes}
            )
            assert response.status_code == status, response.text
            assert detail in response.json()["detail"]
        assert await _rows(env, "memory_mutations") == []
        assert await _rows(env, "documents") == []
