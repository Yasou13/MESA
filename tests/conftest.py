"""
Shared test fixtures for the MESA test suite.

Provides deterministic, text-seeded embedding generation so that
distance/novelty calculations in ValenceMotor and retrieval pipelines
are actually tested with mathematically distinct vectors.
"""

import hashlib
import math


def deterministic_embedding(text: str, dim: int = 768) -> list[float]:
    """Generate a deterministic, normalized embedding from input text.

    Uses SHA-256 to derive ``dim`` float values from the text, then
    L2-normalizes the result to a unit vector.  Guarantees:

    - **Deterministic**: Same text always produces the same vector.
    - **Distinct**: Different texts produce measurably different vectors.
    - **Normalized**: ``sum(x**2) ≈ 1.0`` (valid for cosine similarity).

    Args:
        text: Seed string for vector generation.
        dim: Dimensionality of the output vector.

    Returns:
        A list of ``dim`` floats representing a unit vector.
    """
    raw_floats: list[float] = []
    # Chain SHA-256 digests to fill the required dimensionality
    counter = 0
    while len(raw_floats) < dim:
        digest = hashlib.sha256(f"{text}:{counter}".encode()).digest()
        # Each byte → float in [-1.0, 1.0)
        for byte in digest:
            if len(raw_floats) >= dim:
                break
            raw_floats.append((byte / 127.5) - 1.0)
        counter += 1

    # L2 normalize to unit vector
    magnitude = math.sqrt(sum(x * x for x in raw_floats))
    if magnitude == 0:
        return [0.0] * dim
    return [x / magnitude for x in raw_floats]
