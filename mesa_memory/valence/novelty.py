import asyncio
import numpy as np
from pyod.models.ecod import ECOD
from sklearn.metrics.pairwise import cosine_similarity

from mesa_memory.config import config


async def calculate_novelty_score(new_embedding: np.ndarray, existing_embeddings: np.ndarray, threshold: float) -> bool:
    new_embedding = np.array(new_embedding).reshape(1, -1)
    existing_embeddings = np.array(existing_embeddings)

    if len(existing_embeddings) == 0:
        return True

    similarities = cosine_similarity(new_embedding, existing_embeddings)
    max_sim = float(np.max(similarities))

    if max_sim < config.bootstrap_cosine_threshold:
        return True

    if len(existing_embeddings) < config.recalibration_interval:
        return max_sim < threshold

    def _fit_and_score():
        clf = ECOD()
        clf.fit(existing_embeddings)
        return clf.decision_function(new_embedding)

    loop = asyncio.get_running_loop()
    anomaly_score = await loop.run_in_executor(None, _fit_and_score)

    return anomaly_score[0] > threshold
