.PHONY: help sync lint format test check run-demo run-ekartoteka run-iprzedszkole run-nju print-ekartoteka-schema print-iprzedszkole-schema print-nju-schema test-e2e-ekartoteka

help:
	@echo "Available targets:"
	@echo "  make sync             Install and synchronize dependencies"
	@echo "  make lint             Run Ruff checks"
	@echo "  make format           Format Python code with Ruff"
	@echo "  make test             Run the test suite"
	@echo "  make check            Run lint and tests"
	@echo "  make run-demo         Run the demo integration"
	@echo "  make run-ekartoteka   Run e-Kartoteka using .env.ekartoteka"
	@echo "  make run-iprzedszkole Run iPrzedszkole using .env.iprzedszkole"
	@echo "  make run-nju          Run NJU Mobile using .env.nju"
	@echo "  make print-ekartoteka-schema  Print the e-Kartoteka category-data JSON Schema"
	@echo "  make print-iprzedszkole-schema Print the iPrzedszkole category-data JSON Schema"
	@echo "  make print-nju-schema         Print the NJU category-data JSON Schema"
	@echo "  make test-e2e-ekartoteka  Run read-only e-Kartoteka E2E tests"

sync:
	uv sync

lint:
	uv run ruff check .

format:
	uv run ruff format .

test:
	uv run pytest

check: lint test

run-demo:
	uv run oblidog-integrations demo

run-ekartoteka:
	@test -f .env.ekartoteka || { echo "Missing .env.ekartoteka; copy .env.ekartoteka.example first."; exit 1; }
	@set -a; . ./.env.ekartoteka; set +a; uv run oblidog-integrations ekartoteka

run-iprzedszkole:
	@test -f .env.iprzedszkole || { echo "Missing .env.iprzedszkole; copy .env.iprzedszkole.example first."; exit 1; }
	@set -a; . ./.env.iprzedszkole; set +a; uv run oblidog-integrations iprzedszkole

run-nju:
	@test -f .env.nju || { echo "Missing .env.nju; copy .env.nju.example first."; exit 1; }
	@set -a; . ./.env.nju; set +a; uv run oblidog-integrations nju

print-ekartoteka-schema:
	@uv run python -m oblidog_integrations.integrations.ekartoteka.schema

print-iprzedszkole-schema:
	@uv run python -m oblidog_integrations.integrations.iprzedszkole.schema

print-nju-schema:
	@uv run python -m oblidog_integrations.integrations.nju.schema

test-e2e-ekartoteka:
	@test -f .env.ekartoteka || { echo "Missing .env.ekartoteka; copy .env.ekartoteka.example first."; exit 1; }
	@set -a; . ./.env.ekartoteka; set +a; uv run pytest -m ekartoteka_e2e
