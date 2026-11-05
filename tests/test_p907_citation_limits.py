"""Citation parsing must stay responsive for queries and stored API-sized text."""

import random
import signal
import time

import pytest

from mesa_storage.legal_identity import LegalEntityResolver


# Wall-clock tests use no_cover so tracing does not consume the 200 ms budget.
# They still run in the coverage job; functional/structural tests stay traced.
def _timed_extract(resolver, text):
    def expired(*_):
        raise AssertionError("citation resolution exceeded 200 ms")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 0.2)
    start = time.perf_counter()
    try:
        citations = resolver.extract_citations(text)
        assert time.perf_counter() - start < 0.2
        return citations
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.mark.parametrize("size", [4096, 32768])
@pytest.mark.parametrize(
    "stem",
    [
        "1",
        " ",
        "TCK/1 ",
        "TCK m. 81 ",
        "TBK 117 CMK 86 ",
        "7201 sayılı Kanunun 21. maddesi ",
        "Tebligat K. 21. madde ",
    ],
)
@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.no_cover
def test_opus_repros_and_dense_matches(size, stem, scoped):
    resolver = LegalEntityResolver(
        law_names={"Tebligat Kanunu": "7201"} if scoped else None
    )
    text = (stem * (size // len(stem) + 1))[:size]
    _timed_extract(resolver, text)


@pytest.mark.parametrize("size", [4096, 32768])
@pytest.mark.parametrize("tail", ["1 x", "1 TCK", "2020/1 E."])
@pytest.mark.no_cover
def test_long_internal_whitespace(size, tail):
    _timed_extract(LegalEntityResolver(), "TBK" + " " * (size - 3 - len(tail)) + tail)


@pytest.mark.no_cover
def test_stored_content_repro_preserves_real_citation():
    citations = _timed_extract(LegalEntityResolver(), "TCK m. 81 " + "1" * (32768 - 10))
    assert [(c.statute_code, c.article) for c in citations] == [("TCK", "81")]


@pytest.mark.no_cover
def test_seeded_citation_grammar_fuzz_at_api_limits():
    rng = random.Random(907)
    tokens = [
        "1",
        "2020",
        "12345678901234567890",
        " ",
        "\t",
        "\n",
        " " * 500,
        "m.",
        "md.",
        "madde",
        "TCK",
        "TBK",
        "AY",
        "5237",
        "7201",
        "Tebligat Kanunu",
        "sayılı",
        "Kanunun",
        "inci",
        "ıncı",
        "uncu",
        "üncü",
        "nci",
        "maddesi",
        "uyarınca",
        ".",
        ",",
        ";",
        "/",
        "-",
        "ve",
    ]
    resolvers = [
        LegalEntityResolver(),
        LegalEntityResolver(
            law_names={"Tebligat Kanunu": "7201", "Zümrüt Taşları Kanunu": "8888"}
        ),
    ]
    for size in (4096, 32768):
        for _ in range(24):
            parts = []
            length = 0
            while length < size:
                token = rng.choice(tokens)
                parts.append(token)
                length += len(token)
            for resolver in resolvers:
                _timed_extract(resolver, "".join(parts)[:size])


@pytest.mark.no_cover
def test_many_distinct_articles_and_possessive_duration_guards():
    # Exercise the match loops, including duration lookahead at every alias.
    for stem in (
        "7201 sayılı Kanunun {i}. maddesi ",
        "TBK'nın {i} ay ",
        "TBK m. {i} - {i} CMK m. {i}; ",
    ):
        text = "".join(stem.format(i=i) for i in range(1600))[:32768]
        _timed_extract(LegalEntityResolver(), text)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("117 TBK, CMK m. 86", [("CMK", "86"), ("TBK", "117")]),
        ("TBK 117 CMK 86", [("TBK", "117"), ("CMK", "86")]),
        ("TCK m. 81 ve m. 82 - 83", [("TCK", "81"), ("TCK", "82"), ("TCK", "83")]),
        ("TBK'nın 5 ay süre şartı ve 3 ay", [("TBK", None)]),
        (
            "7201 sayılı Kanunun 21. maddesi ve TCK m. 81",
            [("7201", "21"), ("TCK", "81")],
        ),
        ("TBK ve 49. madde uyarınca TBK", [("TBK", None), ("TBK", "49")]),
        ("21 1 TBK 1 CMK", [("CMK", None), ("TBK", "1")]),
        ("6098 81 madde 6098 TCK", [("TCK", None), ("TBK", "81")]),
        ("TBK TBK 81.", [("TBK", "81")]),
        ("117 TBK 117 CMK", [("TBK", "117"), ("CMK", None)]),
    ],
)
def test_normal_citations_keep_order_and_overlap_semantics(text, expected):
    assert [
        (c.statute_code, c.article)
        for c in LegalEntityResolver().extract_citations(text)
    ] == expected


@pytest.mark.parametrize("text", ["1" * 32760 + " TCK", "TCK m. " + "1" * 32760])
@pytest.mark.no_cover
def test_overlong_digit_runs_do_not_become_partial_articles(text):
    assert all(c.article is None for c in _timed_extract(LegalEntityResolver(), text))


def test_citation_patterns_have_no_unbounded_repetitions():
    # Timing repros cover observed costs; this also guards the cause of the
    # single regex call that held the GIL. Optional clauses can contain bounded
    # tokens, but no run can grow with the entire input.
    from re import _constants, _parser

    def audit(tree):
        for op, args in tree:
            if op in (
                _constants.MAX_REPEAT,
                _constants.MIN_REPEAT,
                _constants.POSSESSIVE_REPEAT,
            ):
                assert args[1] != _constants.MAXREPEAT
                audit(args[2])
            elif op is _constants.SUBPATTERN:
                audit(args[-1])
            elif op is _constants.BRANCH:
                for branch in args[1]:
                    audit(branch)
            elif op in (_constants.ASSERT, _constants.ASSERT_NOT):
                audit(args[1])

    resolver = LegalEntityResolver(law_names={"Tebligat Kanunu": "7201"})
    for name in (
        "_forward_pattern",
        "_reverse_pattern",
        "_continued_article_pattern",
        "_numbered_law_pattern",
        "_scope_name_pattern",
        "_duration_pattern",
    ):
        pattern = getattr(resolver, name)
        audit(_parser.parse(pattern.pattern, pattern.flags))
