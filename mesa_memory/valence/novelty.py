import asyncio
import logging

import numpy as np
from pyod.models.ecod import ECOD
from sklearn.metrics.pairwise import cosine_similarity

from mesa_memory.config import config

logger = logging.getLogger("MESA_Novelty")


def _normalize_ecod_score(raw_score: float, all_scores: np.ndarray) -> float:
    """Normalize a raw ECOD anomaly score to the [0, 1] range via Min-Max scaling.

    ECOD produces unbounded, distribution-dependent anomaly scores.  To make
    them comparable to a fixed threshold, we rescale against the score
    distribution of the training set.

    Edge cases:
    - If all training scores are identical (max == min), returns 0.5 to avoid
      division-by-zero — the sample is indistinguishable from the population.
    """
    s_min = float(np.min(all_scores))
    s_max = float(np.max(all_scores))

    if s_max == s_min:
        return 0.5

    normalized = (raw_score - s_min) / (s_max - s_min)
    return float(np.clip(normalized, 0.0, 1.0))


async def calculate_novelty_score(
    new_embedding: np.ndarray,
    existing_embeddings: np.ndarray,
    cosine_threshold: float,
) -> bool:
    """Evaluate whether a new embedding is novel relative to the existing pool.

    Decision pipeline:
    1. **Cold start** (no existing embeddings): Always novel.
    2. **Cosine fast-path**: If max cosine similarity to existing embeddings is
       below ``bootstrap_cosine_threshold``, the sample is clearly novel — skip
       the expensive ECOD fit.
    3. **Bootstrap phase** (fewer than ``recalibration_interval`` embeddings):
       Use cosine similarity against the adaptive ``cosine_threshold``.
    4. **Steady-state ECOD**: Fit an ECOD model on existing embeddings, score
       the new sample, normalize to [0, 1] via min-max scaling, and compare
       against the independent ``ecod_anomaly_threshold``.

    Args:
        new_embedding:       The candidate embedding vector.
        existing_embeddings: The historical embedding matrix.
        cosine_threshold:    Adaptive threshold for cosine-only evaluation
                             (used in fast-path and bootstrap phases).

    Returns:
        ``True`` if the embedding is novel and should be admitted.
    """
    new_embedding = np.array(new_embedding).reshape(1, -1)
    existing_embeddings = np.array(existing_embeddings)

    if len(existing_embeddings) == 0:
        return True

    similarities = cosine_similarity(new_embedding, existing_embeddings)
    max_sim = float(np.max(similarities))

    if max_sim < config.bootstrap_cosine_threshold:
        return True

    if len(existing_embeddings) < config.recalibration_interval:
        return max_sim < cosine_threshold

    def _fit_and_score():
        clf = ECOD()
        clf.fit(existing_embeddings)
        new_score = clf.decision_function(new_embedding)[0]
        train_scores = clf.decision_scores_
        return new_score, train_scores

    loop = asyncio.get_running_loop()
    raw_score, train_scores = await loop.run_in_executor(None, _fit_and_score)

    normalized_score = _normalize_ecod_score(raw_score, train_scores)

    logger.debug(
        "ECOD novelty: raw=%.4f, normalized=%.4f, threshold=%.4f",
        raw_score,
        normalized_score,
        config.ecod_anomaly_threshold,
    )

    return normalized_score > config.ecod_anomaly_threshold
