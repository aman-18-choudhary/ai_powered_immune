# ScamShield Immune: developer and delivery targets. `make help` lists them.
# Everything is simulator-only; the multi-container stack (`stack-*`) has not been run in the
# environment this repository was built in (no Docker daemon): see README.md.

SHELL := /bin/bash
VENV ?= .venv
PY ?= $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)
PORT ?= 8080
COMPOSE_FILE := infra/docker-compose.yml
COMPOSE := docker compose -f $(COMPOSE_FILE) --profile core --profile stack
ENV_FILE := infra/.env

# Python packages in dependency order (editable installs).
PY_PKGS := contracts libs/svckit services/sim_engine services/call_guard services/txn_guard \
  services/antibody_hub services/evidence_ledger services/graph_twin services/geo_intel \
  services/gateway \
  tests/e2e/bench tests/e2e/audit services/demo_server
# Packages whose [postgres] extra is installed too (psycopg2-binary; optional, used by the stack).
LINT_PKGS := $(PY_PKGS)

.PHONY: help venv install test-all lint demo demo-dev demo-server console-dev console-build \
  stack-env stack-up stack-down stack-logs stack-hero stack-config bench bench-full \
  screenshots verify-package check clean up down test

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/' | awk -F'\t' '{printf "  %-16s %s\n", $$1, $$2}'

venv: ## Create .venv (Python 3.11+) and upgrade pip
	@command -v python3.11 >/dev/null && python3.11 -m venv $(VENV) || python3 -m venv $(VENV)
	$(VENV)/bin/python -m pip install --upgrade pip
	@echo "venv ready: source $(VENV)/bin/activate"

install: ## Editable installs of every package (dependency order) + console npm ci
	@test -x $(VENV)/bin/python || $(MAKE) venv
	@for p in $(PY_PKGS); do \
	  echo "== pip install -e $$p[dev]"; \
	  $(VENV)/bin/python -m pip install -q -e "$$p[dev]" || exit 1; \
	done
	cd web-console && npm ci --no-audit --no-fund

test-all: ## Every pytest suite (-W error) + console unit tests; pass/fail summary, non-zero on failure
	@# Long suites: txn_guard ~90 s, graph_twin ~55 s, demo_server ~50 s, bench ~60 s, call_guard ~25 s.
	PYTHON=$(PY) scripts/test_all.sh

lint: ## ruff check + format --check on python packages; eslint + tsc on the console
	@fail=0; for p in $(LINT_PKGS); do \
	  echo "== ruff $$p"; \
	  (cd $$p && $(abspath $(PY)) -m ruff check . && $(abspath $(PY)) -m ruff format --check .) || fail=1; \
	done; \
	echo "== console eslint + tsc"; \
	(cd web-console && npx --no-install eslint . && npx --no-install tsc --noEmit) || fail=1; \
	exit $$fail

# ---- demo (the verified path) ------------------------------------------------------------------
console-build:
	cd web-console && npm install --no-audit --no-fund && npm run build

demo: console-build ## Build the console and serve it with the demo-server on http://localhost:$(PORT)
	@echo "ScamShield Immune demo: http://localhost:$(PORT)  (simulated data; press Play)"
	DEMO_STATIC_DIR=$(CURDIR)/web-console/dist PORT=$(PORT) $(PY) -m demo_server

demo-server: ## Demo-server only (API + stream) on :$(PORT)
	PORT=$(PORT) $(PY) -m demo_server

console-dev: ## Vite dev server on :5173 proxying /api to :8080
	cd web-console && npm install --no-audit --no-fund && npm run dev

demo-dev: ## Demo-server in the background on :$(PORT) + Vite dev server (Ctrl-C stops both)
	@trap 'kill 0' INT TERM EXIT; \
	PORT=$(PORT) $(PY) -m demo_server & \
	cd web-console && npm install --no-audit --no-fund && DEMO_API=http://localhost:$(PORT) npm run dev; \
	wait

# ---- multi-container stack (NOT exercised in the build environment: no Docker daemon) ------------
stack-env:
	@test -f $(ENV_FILE) || { echo "missing $(ENV_FILE): cp infra/.env.example $(ENV_FILE) and replace the DEMO-ONLY values"; exit 1; }

stack-config: stack-env ## Validate the compose file with the real env (no containers started)
	docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE) --profile core --profile stack --profile observability config -q && echo "compose config OK"

stack-up: stack-env ## docker compose up -d --build (core + stack profiles)
	$(COMPOSE) up -d --build

stack-down: ## docker compose down (volumes kept; add V=1 to also delete them)
	$(COMPOSE) --profile observability down $(if $(V),-v,)

stack-logs: ## Follow logs (SERVICE=name for one service)
	$(COMPOSE) logs -f --tail=100 $(SERVICE)

stack-hero: ## Start the hero scenario on sim-engine (stack must be up)
	$(COMPOSE) exec sim-engine python -c "import urllib.request as u; r=u.Request('http://127.0.0.1:8000/scenarios/hero', data=b'{}', headers={'content-type':'application/json'}, method='POST'); print(u.urlopen(r).read().decode())"

# ---- benchmark, evidence, screenshots ----------------------------------------------------------
bench: ## Quick benchmark (no sensitivity, no hero seeds) -> docs/benchmark_report.quick.md (the committed docs/benchmark_report.md is the full run)
	$(PY) -m scam_bench.run_benchmark --seed 42 --no-sensitivity --hero-seeds 0 --out docs/benchmark_report.quick.md

bench-full: ## Full benchmark (default ablations, sensitivity, 40 hero seeds; ~3-4 min) -> docs/benchmark_report.md
	$(PY) -m scam_bench.run_benchmark --seed 42 --out docs/benchmark_report.md

verify-package: ## Build the hero package via the demo-server code path and verify it with verify.py, pinned
	$(PY) scripts/verify_package.py 1

screenshots: ## Regenerate docs/screenshots against a running `make demo` (CONSOLE_URL to override; needs Playwright browsers)
	cd web-console && CONSOLE_URL=$${CONSOLE_URL:-http://localhost:$(PORT)} node e2e/shots.mjs && python3 e2e/optimise-shots.py

check: ## Static checks: Dockerfiles, markdown links, compose config with the example env
	$(PY) scripts/check_dockerfiles.py
	$(PY) scripts/check_links.py
	docker compose -f $(COMPOSE_FILE) --env-file infra/.env.example --profile core --profile stack --profile observability config -q && echo "compose config OK"

clean: ## Remove caches and build output (keeps .venv and node_modules)
	find . -name __pycache__ -type d -not -path "./.venv/*" -not -path "*/node_modules/*" -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache web-console/dist web-console/test-results web-console/playwright-report
	find . -name .pytest_cache -type d -not -path "./.venv/*" -not -path "*/node_modules/*" -prune -exec rm -rf {} +

# ---- compatibility aliases (earlier skeleton) ------------------------------------------------------
up: stack-up
down: stack-down
test: test-all
