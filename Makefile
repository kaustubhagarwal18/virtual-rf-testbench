PACKAGES := simulators drivers measurements tools triage
COVERAGE_FLOOR := 80
EVAL_REPORT ?= triage/results/rag.md
FIRMWARE_VERSION ?= $(shell uv version --short 2>/dev/null)
REVISION ?= $(shell git rev-parse --short HEAD 2>/dev/null)

export FIRMWARE_VERSION REVISION
PYTHON_PATHS := $(PACKAGES) tests

.PHONY: setup lint fmt test summary bench-up bench-down bench-logs bench-test bench-summary fault fault-dataset triage triage-demo eval clean

setup:
	uv sync
	uv run pre-commit install

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run pylint $(PYTHON_PATHS)
	uv run pydoclint $(PACKAGES)
	uv run bandit -q -c pyproject.toml -r $(PACKAGES)
	uv run codespell
	uv run hadolint Dockerfile
	uv run mbake validate Makefile
	uv run mbake format --check Makefile

fmt:
	uv run ruff check --fix-only .
	uv run ruff format .
	uv run mbake format Makefile

test:
	uv run pytest --cov-fail-under=$(COVERAGE_FLOOR) --junitxml=reports/junit.xml --cov-report=xml:reports/coverage.xml

summary:
	uv run python -m tools.results_summary reports/junit.xml --coverage reports/coverage.xml --coverage-floor $(COVERAGE_FLOOR)

bench-up:
	docker compose up --build --detach --wait --wait-timeout 90

bench-down:
	docker compose down --remove-orphans

bench-logs:
	docker compose logs --follow

bench-test:
	uv run pytest tests/bench --no-cov --junitxml=reports/bench-junit.xml

bench-summary:
	uv run python -m tools.results_summary reports/bench-junit.xml

fault:
	uv run python -m tools.fault_run $(NAME) $(if $(SIZE),--size $(SIZE)) --out results/faults

fault-dataset:
	uv run python -m tools.fault_run --dataset triage/data/runs

triage:
	uv run python -m triage.service $(RUN)

triage-demo:
	uv run python -m tools.triage_demo $(RUN)

eval:
	uv run python -m triage.evaluate $(if $(MODEL),--model $(MODEL)) --report $(EVAL_REPORT)

clean:
	-uv run --no-sync pre-commit uninstall
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov reports
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
