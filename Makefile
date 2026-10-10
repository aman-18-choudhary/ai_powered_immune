.PHONY: up down test lint demo-server console-dev console-build demo

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

# Web console (web-console/): Vite dev server on :5173 proxying /api (HTTP + WebSocket) to :8080.
console-dev:
	cd web-console && npm install --no-audit --no-fund && npm run dev

console-build:
	cd web-console && npm install --no-audit --no-fund && npm run build

# One URL: build the console and serve it from the demo server (simulated data).
demo: console-build
	@echo "ScamShield Immune demo: http://localhost:$${PORT:-8080}"
	DEMO_STATIC_DIR=$(CURDIR)/web-console/dist PORT=$${PORT:-8080} python -m demo_server
