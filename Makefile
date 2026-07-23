.PHONY: install dev v4-dev test check bench docker-up v4-docker-up health zero-cost-dev

UV ?= uv

install:
	$(UV) sync --locked --extra dev

dev:
	MESA_RUNTIME_PROFILE=api-only $(UV) run python -m mesa_memory.runtime_entrypoint

v4-dev:
	MESA_RUNTIME_PROFILE=combined $(UV) run python -m mesa_memory.runtime_entrypoint

zero-cost-dev:
	MESA_ZERO_COST_MODE=true MESA_RUNTIME_PROFILE=combined $(UV) run python -m mesa_memory.runtime_entrypoint

test:
	$(UV) run pytest -q

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
