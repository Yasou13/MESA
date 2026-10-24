from mesa_memory.retrieval.core import normalize_query


def test_normalize_query():
    assert normalize_query("  Hello   World  ") == "hello world"
    assert normalize_query("  multiple   spaces   between words ") == "multiple spaces between words"
