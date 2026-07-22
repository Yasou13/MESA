# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.13.5-slim-bookworm@sha256:4c2cf9917bd1cbacc5e9b07320025bdb7cdf2df7b0ceaccb55e9dd7e30987419
FROM ghcr.io/astral-sh/uv:0.11.30@sha256:93b61e21202b1dab861092748e46bbd6e0e41dd84f59b9174efd2353186e1b47 AS uv

FROM ${PYTHON_IMAGE} AS builder
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
RUN uv export --quiet --frozen --no-dev --no-emit-project --output-file=/tmp/requirements.txt >/dev/null \
    && python -m pip wheel --no-cache-dir --wheel-dir=/wheels -r /tmp/requirements.txt \
    && python -m pip wheel --no-cache-dir --no-deps --wheel-dir=/wheels .

FROM ${PYTHON_IMAGE} AS runtime
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
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels /wheels/mesa_memory-*.whl && rm -rf /wheels
USER mesa:mesa
WORKDIR /var/lib/mesa
VOLUME ["/var/lib/mesa"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "mesa_memory.container_health"]
ENTRYPOINT ["python", "-m", "mesa_memory.runtime_entrypoint"]
