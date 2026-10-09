.PHONY: up down test lint demo-server

PKGS := $(patsubst %/pyproject.toml,%,$(shell find . -name pyproject.toml -not -path "./.venv/*" -not -path "*/node_modules/*"))

up:
	docker compose -f infra/docker-compose.yml up -d

down:
	docker compose -f infra/docker-compose.yml down

test:
	@for p in $(PKGS); do echo "== pytest $$p"; (cd $$p && python -m pytest -q) || exit 1; done

lint:
	@for p in $(PKGS); do echo "== ruff $$p"; (cd $$p && ruff check .) || exit 1; done

# Single-process demo: the console's /api + /api/stream backed by the real service code (simulated
# data). Set DEMO_STATIC_DIR to a built web console to serve it from the same process.
demo-server:
	PORT=$${PORT:-8080} python -m demo_server
