# Build from the repository root:
# docker build -f mesa-benchmark/Dockerfile -t mesa-benchmark .
FROM python:3.10-slim-bookworm

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/mesa-benchmark \
    HF_HOME=/opt/mesa-model-cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

COPY mesa-benchmark/requirements-lock.txt /app/requirements-lock.txt
RUN pip install --no-cache-dir -r /app/requirements-lock.txt
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

COPY pyproject.toml MANIFEST.in README.md /app/
COPY mesa_memory /app/mesa_memory
COPY mesa_storage /app/mesa_storage
COPY mesa-benchmark /app/mesa-benchmark
RUN pip install --no-cache-dir --no-deps /app

RUN mkdir -p /app/results
ENTRYPOINT ["mesa-benchmark", "run"]
CMD ["--config", "mesa-benchmark/config.yaml"]
