# ──────────────────────────────────────────────────────────────
# MESA Memory System – Production API Container
# ──────────────────────────────────────────────────────────────
FROM python:3.10-slim

# Prevent Python from writing .pyc files and enable unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ── System dependencies (build-essential needed for some C-extension wheels) ──
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc build-essential && \
    rm -rf /var/lib/apt/lists/*

# ── Python dependencies (cached layer — only rebuilds when requirements change) ──
COPY requirements-core.txt .
RUN pip install --no-cache-dir -r requirements-core.txt

# ── Pre-download spaCy language model (prevents runtime downloads in air-gapped envs) ──
RUN python -m spacy download en_core_web_sm

# ── Application code ──
COPY mesa_memory/ ./mesa_memory/
COPY .env.example .env.example

# ── Persistent storage mount point ──
RUN mkdir -p /app/storage
VOLUME ["/app/storage"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "mesa_memory.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
