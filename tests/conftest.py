"""
Shared test fixtures for the MESA test suite.

Provides:
- Dynamic path resolution for test storage directories (CI-safe).
- Deterministic, text-seeded embedding generation so that
  distance/novelty calculations in ValenceMotor and retrieval pipelines
  are actually tested with mathematically distinct vectors.
"""

import hashlib
import math
import os
import shutil
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Dynamic path resolution — resolves from *this file's* location, not CWD.
# Works identically on developer machines, CI runners, and containers.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_STORAGE_BASE = REPO_ROOT / ".test_storage_tmp"


def make_test_storage_dir(name: str) -> str:
    """Return an absolute path to a named test storage subdirectory.

    All test storage is rooted under ``<repo>/.test_storage_tmp/<name>/``,
    ensuring isolation between test modules and deterministic cleanup.
    """
    path = TEST_STORAGE_BASE / name
    return str(path)


@pytest.fixture()
def tmp_storage_dir(request):
    """Pytest fixture: create a unique, auto-cleaned test storage directory.

    The directory name is derived from the test node ID to avoid collisions
    between parallel test modules.  Torn down after the test completes.
    """
    dir_name = request.node.name.replace("[", "_").replace("]", "_").replace("/", "_")
    path = TEST_STORAGE_BASE / dir_name
    os.makedirs(path, exist_ok=True)
    yield str(path)
    shutil.rmtree(path, ignore_errors=True)


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
