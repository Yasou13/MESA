"""Optional, lazily loaded cross-encoder for already authorized passages."""

import importlib.util
import threading
from typing import Any


class CrossEncoderReranker:
    def __init__(self, model: str) -> None:
        if importlib.util.find_spec("sentence_transformers") is None:
            raise RuntimeError('Reranking requires: pip install "mesa-memory[ml]"')
        self._model_name = model
        self._model: Any = None
        self._load_lock = threading.Lock()

    def score(self, query: str, texts: list[str]) -> list[float]:
        """Called in a worker thread, including the one-time model load."""
        with self._load_lock:
            if self._model is None:
                from sentence_transformers import CrossEncoder

                self._model = CrossEncoder(self._model_name, max_length=1024)
        return [
            float(value) for value in self._model.predict(
                [(query, text) for text in texts], show_progress_bar=False
            )
        ]
