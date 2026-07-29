# MESA Bağımlılık Envanteri

## Python çekirdek bağımlılıkları

| Bağımlılık | Beyan | `uv.lock` çözümü |
|---|---|---|
| `aiosqlite` | `aiosqlite>=0.22.0` | `0.22.1` |
| `anyio` | `anyio>=4.0.0` | `4.14.2` |
| `httpx` | `httpx>=0.28.0` | `0.28.1` |
| `mcp` | `mcp>=1.0.0` | `1.28.1` |
| `cryptography` | `cryptography>=42.0.0` | `49.0.0` |
| `uvicorn` | `uvicorn>=0.29.0` | `0.51.0` |
| `fastapi` | `fastapi>=0.111.0` | `0.139.2` |
| `slowapi` | `slowapi>=0.1.9` | `0.1.10` |
| `prometheus_client` | `prometheus_client>=0.20.0` | `kilitte bulunamadı` |
| `pydantic` | `pydantic>=2.13.0` | `2.13.4` |
| `pydantic-settings` | `pydantic-settings>=2.14.0` | `2.14.2` |
| `lancedb` | `lancedb>=0.30.0` | `0.34.0` |
| `pyarrow` | `pyarrow>=24.0.0` | `25.0.0` |
| `rocksdict` | `rocksdict>=0.3.0` | `0.3.29` |
| `kuzu` | `kuzu>=0.0.11` | `0.11.3` |
| `numpy` | `numpy>=2.2.0` | `2.4.6` |
| `scipy` | `scipy>=1.15.0` | `1.18.0` |
| `scikit-learn` | `scikit-learn>=1.7.0` | `1.9.0` |
| `tenacity` | `tenacity>=9.0.0` | `9.1.4` |
| `psutil` | `psutil>=7.0.0` | `7.2.2` |
| `rich` | `rich>=15.0.0` | `15.0.0` |
| `structlog` | `structlog>=24.0.0` | `26.1.0` |
| `python-dotenv` | `python-dotenv>=1.2.0` | `1.2.2` |
| `tiktoken` | `tiktoken>=0.12.0` | `0.13.0` |
| `tqdm` | `tqdm>=4.67.0` | `4.69.0` |
| `uuid6` | `uuid6>=2025.0.0` | `2025.0.1` |
| `uuid7` | `uuid7>=0.1.0` | `0.1.0` |
| `alembic` | `alembic>=1.13.0` | `1.18.5` |
| `jsonschema` | `jsonschema>=4.26.0` | `4.26.0` |
| `pyyaml` | `PyYAML>=6.0.0` | `6.0.3` |
| `pyod` | `pyod>=3.3.0` | `3.6.2` |
| `numba` | `numba>=0.65.0` | `0.66.0` |
| `llvmlite` | `llvmlite>=0.47.0` | `0.48.0` |

## Python optional grup: `adapters`

| Bağımlılık | Beyan | `uv.lock` çözümü |
|---|---|---|
| `anthropic` | `anthropic>=0.100.0` | `0.117.0` |
| `openai` | `openai>=2.36.0` | `2.46.0` |
| `ollama` | `ollama>=0.6.1` | `0.6.2` |
| `groq` | `groq>=0.9.0` | `1.5.0` |
| `litellm` | `litellm>=1.40.0` | `1.93.0` |

## Python optional grup: `ml`

| Bağımlılık | Beyan | `uv.lock` çözümü |
|---|---|---|
| `torch` | `torch>=2.11.0` | `2.13.0` |
| `transformers` | `transformers>=5.8.0` | `5.14.1` |
| `tokenizers` | `tokenizers>=0.22.0` | `0.22.2` |
| `safetensors` | `safetensors>=0.7.0` | `0.8.0` |
| `huggingface_hub` | `huggingface_hub>=1.14.0` | `kilitte bulunamadı` |
| `sentence-transformers` | `sentence-transformers>=3.0.0` | `5.6.0` |
| `spacy` | `spacy>=3.8.0` | `3.8.14` |

## Python optional grup: `mcp`

| Bağımlılık | Beyan | `uv.lock` çözümü |
|---|---|---|

## Python optional grup: `langchain`

| Bağımlılık | Beyan | `uv.lock` çözümü |
|---|---|---|
| `langchain-core` | `langchain-core>=0.2.0` | `1.5.0` |

## Python optional grup: `full`

| Bağımlılık | Beyan | `uv.lock` çözümü |
|---|---|---|
| `outlines` | `outlines>=1.2.0` | `1.3.2` |
| `matplotlib` | `matplotlib>=3.10.0` | `3.11.1` |
| `pillow` | `pillow>=12.0.0` | `12.3.0` |

## Python optional grup: `benchmarks`

| Bağımlılık | Beyan | `uv.lock` çözümü |
|---|---|---|
| `datasets` | `datasets>=2.14.0` | `5.0.0` |
| `sentence-transformers` | `sentence-transformers>=3.0.0` | `5.6.0` |
| `mem0ai` | `mem0ai>=0.1.0` | `2.0.12` |
| `letta-client` | `letta-client>=1.0.0` | `1.12.1` |
| `zep-cloud` | `zep-cloud>=2.0.0` | `3.25.0` |

## Python optional grup: `loadtest`

| Bağımlılık | Beyan | `uv.lock` çözümü |
|---|---|---|
| `locust` | `locust>=2.29.0` | `2.46.0` |

## Python optional grup: `dev`

| Bağımlılık | Beyan | `uv.lock` çözümü |
|---|---|---|
| `pytest` | `pytest>=7.4.0` | `9.1.1` |
| `pytest-asyncio` | `pytest-asyncio>=0.23.0` | `1.4.0` |
| `pytest-cov` | `pytest-cov>=4.1.0` | `7.1.0` |
| `pytest-benchmark` | `pytest-benchmark>=4.0.0` | `5.2.3` |
| `cyclonedx-bom` | `cyclonedx-bom>=5.1.0` | `7.3.0` |
| `mypy` | `mypy>=1.10.0` | `2.3.0` |
| `black` | `black>=24.0.0` | `26.5.1` |
| `ruff` | `ruff>=0.4.0` | `0.15.22` |
| `datasets` | `datasets>=2.14.0` | `5.0.0` |

## Python çözüm grafiği

`uv.lock` içinde **263** paket kaydı vardır. Tam transitive liste için `uv.lock` kaynak dosyası esastır.

## JavaScript: `mesa_dashboard/package.json`

| Tür | Paket | Beyan | Lock çözümü |
|---|---|---|
| `dependencies` | `lucide-react` | `^1.26.0` | `1.26.0` |
| `dependencies` | `react` | `^19.2.7` | `19.2.8` |
| `dependencies` | `react-dom` | `^19.2.7` | `19.2.8` |
| `dependencies` | `react-router-dom` | `^7.18.1` | `7.18.1` |
| `devDependencies` | `@types/node` | `^24.13.2` | `24.13.3` |
| `devDependencies` | `@types/react` | `^19.2.17` | `19.2.17` |
| `devDependencies` | `@types/react-dom` | `^19.2.3` | `19.2.3` |
| `devDependencies` | `@vitejs/plugin-react` | `^6.0.3` | `6.0.4` |
| `devDependencies` | `oxlint` | `^1.71.0` | `1.75.0` |
| `devDependencies` | `typescript` | `~6.0.2` | `6.0.3` |
| `devDependencies` | `vite` | `^8.1.1` | `8.1.5` |
## JavaScript: `mesa-benchmark/dashboard-ui/package.json`

| Tür | Paket | Beyan | Lock çözümü |
|---|---|---|
| `dependencies` | `@fontsource-variable/inter` | `^5.3.0` | `5.3.0` |
| `dependencies` | `react` | `^19.1.1` | `19.2.8` |
| `dependencies` | `react-dom` | `^19.1.1` | `19.2.8` |
| `devDependencies` | `@playwright/test` | `^1.55.0` | `1.61.1` |
| `devDependencies` | `@testing-library/jest-dom` | `^6.6.3` | `6.9.1` |
| `devDependencies` | `@testing-library/react` | `^16.3.0` | `16.3.2` |
| `devDependencies` | `@types/react` | `^19.1.10` | `19.2.17` |
| `devDependencies` | `@types/react-dom` | `^19.1.7` | `19.2.3` |
| `devDependencies` | `@vitejs/plugin-react` | `^5.0.2` | `5.2.0` |
| `devDependencies` | `jsdom` | `^26.1.0` | `26.1.0` |
| `devDependencies` | `typescript` | `^5.9.2` | `5.9.3` |
| `devDependencies` | `vite` | `^7.1.3` | `7.3.6` |
| `devDependencies` | `vitest` | `^3.2.4` | `3.2.7` |
