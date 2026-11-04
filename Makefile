PACKAGES := simulators drivers measurements tools
COVERAGE_FLOOR := 80
FIRMWARE_VERSION ?= $(shell uv version --short 2>/dev/null)
REVISION ?= $(shell git rev-parse --short HEAD 2>/dev/null)

export FIRMWARE_VERSION REVISION
PYTHON_PATHS := $(PACKAGES) tests

.PHONY: setup lint fmt test summary bench-up bench-down bench-logs clean

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

clean:
	-uv run --no-sync pre-commit uninstall
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov reports
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
