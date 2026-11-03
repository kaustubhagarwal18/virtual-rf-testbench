PACKAGES := simulators drivers
PYTHON_PATHS := $(PACKAGES) tests

.PHONY: setup lint fmt test clean

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
	uv run mbake validate Makefile
	uv run mbake format --check Makefile

fmt:
	uv run ruff format .
	uv run ruff check --fix-only .
	uv run mbake format Makefile

test:
	uv run pytest --cov-fail-under=80

clean:
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
