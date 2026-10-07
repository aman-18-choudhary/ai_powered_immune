.PHONY: up down test lint

PKGS := $(patsubst %/pyproject.toml,%,$(wildcard */pyproject.toml services/*/pyproject.toml))

up:
	docker compose -f infra/docker-compose.yml up -d

down:
	docker compose -f infra/docker-compose.yml down

test:
	@for p in $(PKGS); do echo "== pytest $$p"; (cd $$p && python -m pytest -q) || exit 1; done

lint:
	@for p in $(PKGS); do echo "== ruff $$p"; (cd $$p && ruff check .) || exit 1; done
