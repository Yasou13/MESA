from mesa_memory.retrieval.adaptive import normalize_planner_query


def test_normalize_query_collapses_whitespace():
    assert normalize_planner_query("  Hello   World  ") == "hello world"
    assert normalize_planner_query("  multiple   spaces   between words ") == (
        "multiple spaces between words"
    )
