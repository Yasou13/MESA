"""Canonical vector fixtures for MESA test suite.

All fixtures are constructed from explicit 2D unit-vector components
padded to the target dimensionality, producing vectors with known
cosine similarity properties:

    cosine(VEC_BASE, VEC_ORTHOGONAL) = 0.0
    cosine(VEC_BASE, VEC_NEAR)       ≈ 0.79
    cosine(VEC_BASE, VEC_MATCH)      ≈ 0.95

These replace the degenerate ``[0.1] * N`` pattern that collapses all
pairwise cosine similarities to 1.0, invalidating distance-based logic.
"""

import math
from typing import List


def _pad(v: list[float], dim: int = 768) -> list[float]:
    """Pad a short vector with zeros to the target dimensionality."""
    return v + [0.0] * (dim - len(v))


# ---------------------------------------------------------------------------
# 768-dim fixtures (standard / Ollama nomic-embed-text)
# ---------------------------------------------------------------------------
VEC_BASE = _pad([1.0, 0.0])
VEC_ORTHOGONAL = _pad([0.0, 1.0])
VEC_NEAR = _pad([0.79, math.sqrt(1 - 0.79**2)])
VEC_MATCH = _pad([0.95, math.sqrt(1 - 0.95**2)])

# ---------------------------------------------------------------------------
# 384-dim fixtures (sentence-transformer / all-MiniLM-L6-v2)
# ---------------------------------------------------------------------------
VEC_BASE_384 = _pad([1.0, 0.0], dim=384)
VEC_ORTHOGONAL_384 = _pad([0.0, 1.0], dim=384)
VEC_NEAR_384 = _pad([0.79, math.sqrt(1 - 0.79**2)], dim=384)
VEC_MATCH_384 = _pad([0.95, math.sqrt(1 - 0.95**2)], dim=384)

# ---------------------------------------------------------------------------
# 1536-dim fixtures (OpenAI text-embedding-ada-002)
# ---------------------------------------------------------------------------
VEC_BASE_1536 = _pad([1.0, 0.0], dim=1536)
VEC_ORTHOGONAL_1536 = _pad([0.0, 1.0], dim=1536)
VEC_NEAR_1536 = _pad([0.79, math.sqrt(1 - 0.79**2)], dim=1536)
VEC_MATCH_1536 = _pad([0.95, math.sqrt(1 - 0.95**2)], dim=1536)


# ---------------------------------------------------------------------------
# Diverse vector generators (for drift / recalibration tests)
# ---------------------------------------------------------------------------


def make_diverse_vectors(n: int, dim: int = 768) -> List[List[float]]:
    """Generate *n* diverse unit vectors with distinct angular directions.

    Uses a fixed seed (42) for deterministic test reproducibility.
    Each vector is L2-normalised so cosine similarity is meaningful.
    """
    import numpy as np

    rng = np.random.default_rng(seed=42)
    vecs: List[List[float]] = []
    for _ in range(n):
        v = rng.standard_normal(dim)
        v = v / np.linalg.norm(v)
        vecs.append(v.tolist())
    return vecs
