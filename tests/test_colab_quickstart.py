import json
import re
from pathlib import Path

from mesa_memory.adapter.factory import parse_llm_spec
from mesa_memory.config import parse_embedding_spec


def test_colab_notebook_is_valid_and_specs_parse() -> None:
    nb_path = Path("examples/colab_quickstart.ipynb")
    assert nb_path.is_file(), "examples/colab_quickstart.ipynb does not exist"

    with open(nb_path, "r", encoding="utf-8") as f:
        nb = json.load(f)

    assert "cells" in nb
    assert len(nb["cells"]) >= 7

    for cell in nb["cells"]:
        if cell.get("cell_type") == "code":
            assert cell.get("outputs") == [], f"Found outputs in cell: {cell}"
            assert cell.get("execution_count") is None

    full_text = nb_path.read_text(encoding="utf-8")
    llm_matches = re.findall(r"MESA_LLM_MODEL=([^\s\\\"',]+)", full_text)
    embed_matches = re.findall(r"MESA_EMBEDDING_MODEL=([^\s\\\"',]+)", full_text)

    assert len(llm_matches) >= 2, f"Expected at least 2 LLM specs, found {llm_matches}"
    assert len(embed_matches) >= 1, f"Expected at least 1 embedding spec, found {embed_matches}"

    for spec in llm_matches:
        provider, model = parse_llm_spec(spec)
        assert provider
        assert model

    for spec in embed_matches:
        provider, model = parse_embedding_spec(spec)
        assert provider
        assert model
