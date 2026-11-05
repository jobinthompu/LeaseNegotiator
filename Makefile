.PHONY: install lint format typecheck security test eval web run server docker-build docker-up clean

PYTHON ?= python3

install:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev,db,vector]"

lint:
	ruff check lease_negotiator tests
	ruff format --check lease_negotiator tests

format:
	ruff check --fix lease_negotiator tests
	ruff format lease_negotiator tests

typecheck:
	mypy lease_negotiator

security:
	bandit -c pyproject.toml -r lease_negotiator
	pip-audit

test:
	pytest -m "not integration" --cov=lease_negotiator --cov-report=term-missing --cov-fail-under=80

eval:
	adk eval lease_negotiator evals/lease_negotiation.test.json --config_file_path evals/eval_config.json --print_detailed_results

web:
	adk web

run:
	adk run lease_negotiator

server:
	lease-negotiator-server

docker-build:
	docker build -t lease-negotiator:latest .

docker-up:
	docker compose up --build

clean:
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov
	find . -type d -name "__pycache__" -exec rm -rf {} +
