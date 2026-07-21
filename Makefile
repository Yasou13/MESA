.PHONY: install dev test bench docker-up health zero-cost-dev

install:
	chmod +x install.sh
	./install.sh

dev:
	python scripts/run_server.py --reload

zero-cost-dev:
	MESA_ZERO_COST_MODE=true python scripts/run_server.py --reload

test:
	pytest tests/ -x

bench:
	python scripts/reproduce_benchmark.py --config mesa-benchmark/config_mini_mesa.yaml --seeds 42

docker-up:
	docker compose up --build -d

health:
	python scripts/health_check.py

load-test:
	locust -f tests/bench/locustfile.py --host=http://localhost:8000
