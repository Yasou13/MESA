# MESA v0.6.1 — Learned Reranking Module (CrossEncoder)
# Provides query-candidate learned scoring using sentence-transformers CrossEncoder.
#
# Architecture:
#   - Lazy loading: model is only imported/loaded on first rerank() invocation.
#   - Async execution: synchronous predict calls run in run_in_executor to ensure zero loop blocking.
#   - Graceful degradation: falls back to original ordering on missing dependencies or load errors.
"""
Learned CrossEncoder reranker for the MESA retrieval pipeline.

Computes semantic relevance scores for (query, document) pairs using a
CrossEncoder model (default: cross-encoder/ms-marco-MiniLM-L-6-v2) run off the
asyncio event loop via ThreadPoolExecutor.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Any

logger = logging.getLogger("MESA_Retrieval")


class CrossEncoderReranker:
    """Async-safe wrapper around sentence-transformers CrossEncoder."""

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        self.model_name = model_name
        self._model: Any | None = None
        self._load_failed: bool = False
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def _ensure_loaded(self) -> bool:
        """Lazy load the CrossEncoder model."""
        if self._model is not None:
            return True
        if self._load_failed:
            return False

        try:
            from sentence_transformers import CrossEncoder

            logger.info("Loading CrossEncoder model: %s", self.model_name)
            self._model = CrossEncoder(self.model_name)
            return True
        except ImportError:
            logger.warning(
                "sentence-transformers not installed. CrossEncoder reranking disabled."
            )
            self._load_failed = True
            return False
        except Exception as exc:
            logger.error(
                "Failed to load CrossEncoder model '%s': %s",
                self.model_name,
                exc,
            )
            self._load_failed = True
            return False

    async def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        top_k: int,
    ) -> list[str]:
        """Rerank candidates using learned CrossEncoder scores.

        Args:
            query: The search query text.
            candidates: List of candidate dicts with at least 'cmb_id' and 'content' keys.
            top_k: Maximum number of cmb_ids to return.

        Returns:
            List of cmb_ids sorted by descending CrossEncoder relevance score.
            If reranking fails or model is unavailable, returns original candidate cmb_ids up to top_k.
        """
        if not candidates or not query.strip():
            return [c.get("cmb_id", "") for c in candidates if c.get("cmb_id", "")][
                :top_k
            ]

        def _predict_and_score() -> list[str]:
            if not self._ensure_loaded() or self._model is None:
                return [c.get("cmb_id", "") for c in candidates if c.get("cmb_id", "")][
                    :top_k
                ]

            pairs: list[tuple[str, str]] = []
            valid_candidates: list[dict[str, Any]] = []

            for c in candidates:
                cmb_id = c.get("cmb_id", "")
                content = c.get("content", "").strip()
                if not cmb_id:
                    continue
                # If content is empty, use entity_name or empty string
                if not content:
                    content = c.get("entity_name", "")
                pairs.append((query, content))
                valid_candidates.append(c)

            if not valid_candidates:
                return []

            try:
                scores = self._model.predict(pairs)
                # Combine valid_candidates with scores and sort descending
                scored = list(zip(valid_candidates, scores))
                scored.sort(key=lambda x: float(x[1]), reverse=True)
                return [item[0]["cmb_id"] for item in scored[:top_k]]
            except Exception as exc:
                logger.error("CrossEncoder predict failed: %s", exc, exc_info=True)
                return [c["cmb_id"] for c in valid_candidates[:top_k]]

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, _predict_and_score)
