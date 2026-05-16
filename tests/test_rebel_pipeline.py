from unittest.mock import MagicMock, patch

import pytest

import mesa_memory.extraction.rebel_pipeline as rp
from mesa_memory.extraction.rebel_pipeline import RebelExtractor, _model_holder


@pytest.fixture(autouse=True)
def reset_holder():
    rp._model_holder._pipeline = None
    yield
    rp._model_holder._pipeline = None


def test_extract_triplets_empty():
    extractor = RebelExtractor()
    assert extractor.extract_triplets("") == []
    assert extractor.extract_triplets("   ") == []


@patch("mesa_memory.extraction.rebel_pipeline.pipeline")
def test_extract_triplets_success(mock_pipeline):
    mock_pipe_instance = MagicMock()
    mock_pipe_instance.return_value = [
        {"generated_text": "<s> <triplet> subject <subj> object <obj> relation </s>"}
    ]
    mock_pipeline.return_value = mock_pipe_instance

    extractor = RebelExtractor()
    triplets = extractor.extract_triplets("test")
    assert len(triplets) == 1
    assert triplets[0] == {"head": "subject", "relation": "relation", "tail": "object"}


@patch("mesa_memory.extraction.rebel_pipeline.pipeline")
def test_extract_triplets_exception(mock_pipeline):
    mock_pipe_instance = MagicMock(side_effect=Exception("GPU OOM"))
    mock_pipeline.return_value = mock_pipe_instance

    extractor = RebelExtractor()
    triplets = extractor.extract_triplets("long text")
    assert triplets == []
    assert len(extractor._rebel_failures) == 1
    assert "GPU OOM" in extractor._rebel_failures[0]["error"]


def test_parse_rebel_output_complex():
    extractor = RebelExtractor()
    text = "<s> <triplet> A <subj> B <obj> rel1 <triplet> C <subj> D <obj> rel2 </s>"
    triplets = extractor._parse_rebel_output(text)
    assert len(triplets) == 2
    assert triplets[0] == {"head": "A", "relation": "rel1", "tail": "B"}
    assert triplets[1] == {"head": "C", "relation": "rel2", "tail": "D"}


@patch.dict(
    "sys.modules",
    {"torch": MagicMock(cuda=MagicMock(is_available=MagicMock(return_value=False)))},
)
def test_rebel_cpu_warning(caplog):
    RebelExtractor()
    assert "REBEL running on CPU" in caplog.text


def test_import_error_for_pipeline():
    original_pipeline = rp.pipeline
    try:
        rp.pipeline = None
        rp._model_holder._pipeline = None
        with pytest.raises(ImportError):
            _model_holder.get_pipeline("x")
    finally:
        rp.pipeline = original_pipeline
