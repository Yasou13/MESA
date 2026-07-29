# Build from the repository root:
# docker build -f mesa-benchmark/Dockerfile -t mesa-benchmark .
FROM python:3.10.20-slim-bookworm@sha256:9643927a6fc74bd81b0f1bbb5cce3cb4a491f46b4c5dbee770f28e575f180015

COPY --from=ghcr.io/astral-sh/uv:0.9.6@sha256:4b96ee9429583983fd172c33a02ecac5242d63fb46bc27804748e38c1cc9ad0d /uv /uvx /bin/

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/mesa-benchmark \
    HF_HOME=/opt/mesa-model-cache \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

# The pinned Python base ships vulnerable setuptools vendored utilities.  Keep
# the runtime tooling fixed independently of the application lockfile.
RUN python -m pip install --no-cache-dir setuptools==83.0.0

COPY pyproject.toml uv.lock MANIFEST.in README.md /app/
COPY mesa_memory /app/mesa_memory
COPY mesa_storage /app/mesa_storage
COPY mesa-benchmark /app/mesa-benchmark
RUN uv sync --frozen --no-dev --extra benchmarks
RUN HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 /app/.venv/bin/python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

RUN groupadd --gid 10001 mesa \
    && useradd --uid 10001 --gid mesa --create-home --shell /usr/sbin/nologin mesa \
    && install --directory --owner mesa --group mesa /app/results /opt/mesa-model-cache \
    && chown -R mesa:mesa /app

USER mesa:mesa
ENTRYPOINT ["/app/.venv/bin/mesa-benchmark", "run"]
CMD ["--config", "resource://configs/legacy/default.yaml"]
