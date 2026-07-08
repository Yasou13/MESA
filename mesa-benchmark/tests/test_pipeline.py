import pytest
from mesa_benchmark.clients.base import BenchmarkResponse
from mesa_benchmark.datasets.schemas import (
    BenchmarkQuestion,
    BenchmarkScenario,
    MemoryContext,
)


@pytest.fixture
def mock_scenario() -> BenchmarkScenario:
    """Produces a mock scenario object for testing."""
    return BenchmarkScenario(
        id="TEST-01",
        name="Test Scenario",
        description="Unit test scenario",
        contexts=[MemoryContext(id="c1", text="MESA projesi 2025'te başladı.")],
        questions=[
            BenchmarkQuestion(
                id="q1",
                query="MESA ne zaman başladı?",
                ground_truth="2025",
                expected_context_ids=["c1"],
                evaluation_strategy="exact_match",
            )
        ],
    )


def test_benchmark_response_model() -> None:
    """Tests that BenchmarkResponse accepts all required fields."""
    resp = BenchmarkResponse(
        answer_text="Test answer",
        retrieved_context_ids=["c1"],
        latency_ms=120.0,
        prompt_tokens=10,
        completion_tokens=5,
        metadata={"test": True},
    )
    assert resp.answer_text == "Test answer"
    assert resp.latency_ms == 120.0


def test_scenario_schema(mock_scenario: BenchmarkScenario) -> None:
    """Tests that BenchmarkScenario can be constructed correctly."""
    assert mock_scenario.id == "TEST-01"
    assert len(mock_scenario.contexts) == 1
    assert len(mock_scenario.questions) == 1
    assert mock_scenario.questions[0].evaluation_strategy == "exact_match"


def test_evaluation_strategy_default() -> None:
    """Tests that evaluation_strategy defaults to exact_match."""
    q = BenchmarkQuestion(id="q1", query="test?", ground_truth="answer")
    assert q.evaluation_strategy == "exact_match"
