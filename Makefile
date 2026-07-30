.PHONY: install dev v4-dev test check bench dashboard-build package docker-up v4-docker-up zero-cost-dev

UV ?= uv

install:
	$(UV) sync --locked --all-packages --all-extras --group dev

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
	$(UV) run mypy packages/mesa-memory/src --ignore-missing-imports --explicit-package-bases --follow-imports=skip
	$(UV) run mypy packages/mesa-benchmark/src

dashboard-build:
	npm ci --ignore-scripts --prefix apps/control-dashboard
	npm run build --prefix apps/control-dashboard
	npm ci --ignore-scripts --prefix apps/benchmark-dashboard
	npm run build --prefix apps/benchmark-dashboard
	$(UV) run python tools/stage_frontends.py

package: dashboard-build
	$(UV) build --all-packages

bench:
	$(UV) run mesa-benchmark dataset-sync --suite smoke
	$(UV) run mesa-benchmark run-suite --suite smoke --results-root results

docker-up:
	docker compose up --build -d

v4-docker-up:
	docker compose -f docker-compose.v4.yml up --build -d

load-test:
	$(UV) run locust -f tests/bench/locustfile.py --host=http://localhost:8000
