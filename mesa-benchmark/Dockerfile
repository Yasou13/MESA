# Build from the repository root:
# docker build -f mesa-benchmark/Dockerfile -t mesa-benchmark .
FROM python:3.10-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.9.6 /uv /uvx /bin/

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/mesa-benchmark \
    HF_HOME=/opt/mesa-model-cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock MANIFEST.in README.md /app/
COPY mesa_memory /app/mesa_memory
COPY mesa_storage /app/mesa_storage
COPY mesa-benchmark /app/mesa-benchmark
RUN uv sync --frozen --no-dev --extra benchmarks
RUN /app/.venv/bin/python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

RUN mkdir -p /app/results
ENTRYPOINT ["/app/.venv/bin/mesa-benchmark", "run"]
CMD ["--config", "resource://configs/legacy/default.yaml"]
