# ──────────────────────────────────────────────────────────────
# MESA Memory System – Production API Container
# ──────────────────────────────────────────────────────────────

# ── BUILDER STAGE ──
FROM python:3.10-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY mesa_memory/ ./mesa_memory/
COPY mesa_storage/ ./mesa_storage/
COPY mesa_workers/ ./mesa_workers/
COPY mesa_api/ ./mesa_api/
COPY mesa_client/ ./mesa_client/

RUN pip install --no-cache-dir --prefix=/install ".[adapters]"


# Pre-download spaCy model in builder
RUN python -m spacy download xx_ent_wiki_sm --target /install/lib/python3.10/site-packages

# ── RUNTIME STAGE ──
FROM python:3.10-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MESA_REBEL_ENABLED=false \
    MESA_EXTRACTION_LANG=tr

WORKDIR /app

# Copy dependencies from builder
COPY --from=builder /install /usr/local
COPY . .

# Non-root user for container security hardening
RUN useradd -m -r -s /bin/false mesa

# Persistent storage mount points
RUN mkdir -p /app/storage /app/.kuzu && chown -R mesa:mesa /app/storage /app/.kuzu
VOLUME ["/app/storage", "/app/.kuzu"]

# Never bake secrets into the image, MESA_API_KEY must be provided at runtime

USER mesa

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/init')" || exit 1

CMD ["uvicorn", "mesa_memory.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
