"""Independent dense-vector RAG baseline with no graph or memory cognition."""

from __future__ import annotations

import hashlib
import math
import re
import time
from typing import Any

from ..datasets.schemas import BenchmarkQuestion, MemoryContext
from .base import AbstractBenchmarkClient, BenchmarkResponse, RetrievedContext


class DenseRagClientAdapter(AbstractBenchmarkClient):
    """A transparent cosine-similarity baseline over the exact input chunks."""

    def __init__(self) -> None:
        self.top_n = 5
        self.embedding_backend = "sentence-transformers"
        self.embedding_model = "all-MiniLM-L6-v2"
        self._model: Any = None
        self._contexts: list[MemoryContext] = []
        self._vectors: list[list[float]] = []

    def initialize(self, config_params: dict[str, Any]) -> None:
        self.top_n = int(config_params.get("top_n", 5))
        self.embedding_backend = str(
            config_params.get("embedding_backend", "sentence-transformers")
        )
        self.embedding_model = str(
            config_params.get("embedding_model", "all-MiniLM-L6-v2")
        )
        if self.embedding_backend == "sentence-transformers":
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(
                    self.embedding_model, local_files_only=True
                )
            except (ImportError, OSError) as exc:
                raise RuntimeError(
                    "Dense RAG requires the pinned local embedding model "
                    f"{self.embedding_model!r}; no fallback is allowed"
                ) from exc
        elif self.embedding_backend != "deterministic-hashing":
            raise ValueError(f"unsupported embedding backend: {self.embedding_backend}")

    def _embed(self, text: str) -> list[float]:
        if self._model is not None:
            vector = self._model.encode(text, normalize_embeddings=True)
            return [float(value) for value in vector]
        dimensions = 384
        vector = [0.0] * dimensions
        for token in re.findall(r"\w+", text.casefold(), flags=re.UNICODE):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def clear_memory(self) -> None:
        self._contexts.clear()
        self._vectors.clear()

    def add_memory(self, context: MemoryContext) -> dict[str, Any]:
        started = time.perf_counter()
        self._contexts.append(context.model_copy(deep=True))
        self._vectors.append(self._embed(context.text))
        return {"latency_ms": (time.perf_counter() - started) * 1000.0}

    def answer(self, question: BenchmarkQuestion) -> BenchmarkResponse:
        started = time.perf_counter()
        query = self._embed(question.query)
        ranked = sorted(
            zip(self._contexts, self._vectors),
            key=lambda pair: sum(a * b for a, b in zip(query, pair[1])),
            reverse=True,
        )[: self.top_n]
        contexts = [
            RetrievedContext(
                id=context.id,
                text=context.text,
                rank=index + 1,
                score=sum(a * b for a, b in zip(query, vector)),
                metadata={"source": "dense-rag"},
            )
            for index, (context, vector) in enumerate(ranked)
        ]
        latency = (time.perf_counter() - started) * 1000.0
        return BenchmarkResponse(
            answer_text="\n\n".join(item.text for item in contexts),
            retrieved_contexts=contexts,
            latency_ms=latency,
            retrieval_latency_ms=latency,
            metadata={
                "embedding_backend": self.embedding_backend,
                "embedding_model": self.embedding_model,
            },
        )

    def storage_size_bytes(self) -> int:
        """Report the in-process float payload used by the transparent index."""
        vector_bytes = sum(len(vector) * 8 for vector in self._vectors)
        context_bytes = sum(
            len(context.id.encode("utf-8")) + len(context.text.encode("utf-8"))
            for context in self._contexts
        )
        return vector_bytes + context_bytes

    def close(self) -> None:
        self.clear_memory()
        self._model = None
