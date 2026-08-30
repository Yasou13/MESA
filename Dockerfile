# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.13.12-slim-bookworm@sha256:a58daefb915e1e03ad48f3ca4df8832065412c5c35cacb9d39f4229184de12b6
FROM ghcr.io/astral-sh/uv:0.12.7@sha256:95f2aa1fe59274951cfe9b0cbc7972e879ff1004bc8945d130a32eb0dbd85945 AS uv

FROM ${PYTHON_IMAGE} AS python-base
# The digest makes the starting filesystem reproducible, while Debian's
# security repository supplies fixed packages published after that digest.
# Both build and runtime stages inherit this patched base so the shipped image
# does not retain known fixed HIGH/CRITICAL OS vulnerabilities.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

FROM python-base AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY mesa_memory ./mesa_memory
COPY mesa_storage ./mesa_storage
COPY mesa_workers ./mesa_workers
COPY mesa_api ./mesa_api
COPY mesa_client ./mesa_client
COPY mesa_evals ./mesa_evals
COPY mesa_mcp ./mesa_mcp
COPY --from=uv /uv /usr/local/bin/uv
# The PyPI Linux torch wheel is CUDA-enabled and pulls multi-gigabyte GPU
# libraries. Runtime images are CPU-only, so use the matching CPU build and
# remove GPU-only transitive requirements from the exported lock set.
RUN uv export --quiet --frozen --no-dev --no-hashes --extra ml --extra adapters --no-emit-project --output-file=/tmp/requirements.txt >/dev/null \
    && sed -i '/^nvidia-/d; /^triton==/d' /tmp/requirements.txt \
    && python -m pip wheel --no-cache-dir --retries 5 --timeout 120 \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      --wheel-dir=/wheels -r /tmp/requirements.txt \
    && python -m pip wheel --no-cache-dir --no-deps --wheel-dir=/wheels .

FROM python-base AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MESA_LOAD_DOTENV=false \
    MESA_MODEL_ENABLED=false \
    MESA_EXTERNAL_PROVIDER_ENABLED=false \
    MESA_PORT=8000
RUN groupadd --system mesa && useradd --system --gid mesa --home-dir /nonexistent --shell /usr/sbin/nologin mesa \
    && mkdir -p /var/lib/mesa && chown mesa:mesa /var/lib/mesa
COPY --from=builder /wheels /wheels
RUN wheel="$(find /wheels -maxdepth 1 -name 'mesa_memory-*.whl' -print -quit)" \
    && test -n "$wheel" \
    && python -m pip install --no-cache-dir --no-index --find-links=/wheels "${wheel}[ml,adapters]" \
    && rm -rf /wheels
USER mesa:mesa
WORKDIR /var/lib/mesa
VOLUME ["/var/lib/mesa"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "mesa_memory.container_health"]
ENTRYPOINT ["python", "-m", "mesa_memory.runtime_entrypoint"]
