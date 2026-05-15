import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from mesa_memory.adapter.base import BaseUniversalLLMAdapter
from mesa_memory.config import config


def _embed_text(text: str, embedder: BaseUniversalLLMAdapter) -> np.ndarray:
    vec = embedder.embed(text)
    return np.array(vec).reshape(1, -1)


def _cosine_sim(vec_a: np.ndarray, vec_b: np.ndarray) -> float:
    return float(cosine_similarity(vec_a, vec_b)[0][0])


def calculate_composite_similarity(
    trip_a: dict,
    trip_b: dict,
    embedder: BaseUniversalLLMAdapter,
    cache: dict | None = None,
) -> float:
    def _get_emb(text: str) -> np.ndarray:
        if cache is not None and text in cache:
            vec = cache[text]
        else:
            vec = embedder.embed(text)
        return np.array(vec).reshape(1, -1)

    emb_head_a = _get_emb(trip_a["head"])
    emb_tail_a = _get_emb(trip_a["tail"])
    emb_rel_a = _get_emb(trip_a["relation"])

    emb_head_b = _get_emb(trip_b["head"])
    emb_tail_b = _get_emb(trip_b["tail"])
    emb_rel_b = _get_emb(trip_b["relation"])

    sim_head = _cosine_sim(emb_head_a, emb_head_b)
    sim_tail = _cosine_sim(emb_tail_a, emb_tail_b)

    if (
        sim_head >= config.entity_similarity_threshold
        and sim_tail >= config.entity_similarity_threshold
    ):
        sim_rel = _cosine_sim(emb_rel_a, emb_rel_b)
        return sim_rel

    sim_head_to_tail = _cosine_sim(emb_head_a, emb_tail_b)
    sim_tail_to_head = _cosine_sim(emb_tail_a, emb_head_b)

    if (
        sim_head_to_tail >= config.entity_similarity_threshold
        and sim_tail_to_head >= config.entity_similarity_threshold
    ):
        sim_rel = _cosine_sim(emb_rel_a, emb_rel_b)
        return sim_rel

    return 0.0


def validate_extraction_pair(
    entities_a: list[dict],
    relations_a: list[dict],
    entities_b: list[dict],
    relations_b: list[dict],
) -> dict:
    names_a = {e["name"].strip().lower() for e in entities_a}
    names_b = {e["name"].strip().lower() for e in entities_b}

    if len(names_a | names_b) == 0:
        entity_sim = 0.0
    else:
        entity_sim = len(names_a & names_b) / len(names_a | names_b)

    triples_a = {
        (
            r["source"].strip().lower(),
            r["target"].strip().lower(),
            r["type"].strip().lower(),
        )
        for r in relations_a
    }
    triples_b = {
        (
            r["source"].strip().lower(),
            r["target"].strip().lower(),
            r["type"].strip().lower(),
        )
        for r in relations_b
    }

    if len(triples_a | triples_b) == 0:
        relation_sim = 0.0
    else:
        relation_sim = len(triples_a & triples_b) / len(triples_a | triples_b)

    passed = (
        entity_sim >= config.entity_similarity_threshold
        and relation_sim >= config.relation_similarity_threshold
    )

    return {
        "entity_similarity": entity_sim,
        "relation_similarity": relation_sim,
        "passed": passed,
        "entities_intersection": list(names_a & names_b),
        "entities_union": list(names_a | names_b),
        "relations_intersection": [
            {"source": s, "target": t, "type": tp}
            for s, t, tp in (triples_a & triples_b)
        ],
        "relations_union": [
            {"source": s, "target": t, "type": tp}
            for s, t, tp in (triples_a | triples_b)
        ],
    }
