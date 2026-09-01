.PHONY: setup test format check_linting lock-upgrade

setup:
	uv sync --group dev

test:
	uv run pytest -q

format:
	uv run ruff format .
	uv run ruff check --fix .

check_linting:
	uv run ruff format --check .
	uv run ruff check .

lock-upgrade:
	uv lock -U
