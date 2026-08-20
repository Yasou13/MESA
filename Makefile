.PHONY: install install-all dev v4-dev test test-local test-all test-adapters check bench docker-up v4-docker-up health zero-cost-dev

UV ?= uv

install:
	$(UV) sync --locked --extra dev

install-all:
	$(UV) sync --locked --extra dev --extra adapters

dev:
	MESA_RUNTIME_PROFILE=api-only $(UV) run python -m mesa_memory.runtime_entrypoint

v4-dev:
	MESA_RUNTIME_PROFILE=combined $(UV) run python -m mesa_memory.runtime_entrypoint

zero-cost-dev:
	MESA_ZERO_COST_MODE=true MESA_RUNTIME_PROFILE=combined $(UV) run python -m mesa_memory.runtime_entrypoint

test: test-local

test-local:
	$(UV) run pytest -q -m "not optional_provider and not optional_mcp and not live_external"

test-all:
	$(UV) run pytest -q

test-adapters:
	$(UV) run pytest -q -m "optional_provider or optional_mcp"

check:
	$(UV) run ruff check .
	$(UV) run mypy mesa_memory mesa_storage mesa_workers mesa_api mesa_client --ignore-missing-imports --explicit-package-bases --follow-imports=skip
	$(UV) run mypy mesa-benchmark/mesa_benchmark

bench:
	$(UV) run mesa-benchmark dataset-sync --suite smoke
	$(UV) run mesa-benchmark run-suite --suite smoke --results-root results

docker-up:
	docker compose up --build -d

v4-docker-up:
	docker compose -f docker-compose.v4.yml up --build -d

health:
	$(UV) run python scripts/health_check.py

load-test:
	$(UV) run locust -f tests/bench/locustfile.py --host=http://localhost:8000
