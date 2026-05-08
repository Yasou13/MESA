import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from mesa_memory.config import config


def recalibrate_threshold(current_threshold: float, existing_embeddings: list) -> float:
    if len(existing_embeddings) < config.recalibration_interval:
        return current_threshold

    embeddings = np.array(existing_embeddings)
    recent = embeddings[-config.recalibration_interval:]
    historical = embeddings[:-config.recalibration_interval]

    if len(historical) == 0:
        return current_threshold

    sim_matrix = cosine_similarity(recent, historical)
    mean_sim = float(np.mean(sim_matrix))

    new_val = (0.2 * mean_sim) + (0.8 * current_threshold)
    new_val = max(0.50, min(0.90, new_val))

    return new_val
