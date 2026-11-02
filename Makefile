.PHONY: install test lint typecheck check serve contracts demo infra
install:
	python tools/dev.py setup
test:
	uv run --frozen --no-sync pytest
lint:
	uv run --frozen --no-sync ruff check .
	uv run --frozen --no-sync ruff format --check .
typecheck:
	uv run --frozen --no-sync mypy
check:
	python tools/dev.py check
serve:
	python tools/dev.py serve
contracts:
	uv run --frozen --no-sync python -m app.cli.main validate dataset-manifest specs/examples/dataset-manifest.json
demo:
	python tools/dev.py demo $(APPROVE_DEMO)
infra:
	python tools/dev.py infra
