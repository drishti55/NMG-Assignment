# Project Relay. Run `make help` for targets.
SHELL := /bin/bash
.DEFAULT_GOAL := help

INBOX ?= data/northwind/tickets.jsonl
RUNS  ?= 5
BY    ?= $(shell whoami)
# Docker Desktop on macOS does not always put the CLI on PATH.
DOCKER ?= $(shell command -v docker 2>/dev/null || echo /Applications/Docker.app/Contents/Resources/bin/docker)
COMPOSE := $(DOCKER) compose
RELAY := uv run --frozen relay

.PHONY: help demo check-deps db install migrate seed run worker approve reject cases eval test api frontend dev health report export down clean

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[32m%-10s\033[0m %s\n", $$1, $$2}'

demo: check-deps db install migrate seed ## One command: Postgres, migrations, KB embeddings, both inboxes, approval queue
	@start=$$(date +%s); \
	$(RELAY) run --inbox data/northwind/tickets.jsonl && \
	$(RELAY) run --inbox data/harbor/tickets.jsonl && \
	echo "" && echo "=== Approval queue (relay approvals approve <id> --by <name>) ===" && \
	$(RELAY) approvals list -v && \
	echo "" && echo "=== Escalation and dead-letter cases ===" && $(RELAY) cases && \
	echo "" && echo "Inboxes processed in $$(( $$(date +%s) - start ))s. Outputs: out/decisions.jsonl" && \
	echo "Next: make approve ID=<approval_id>  |  make reject ID=<id> REASON='...'  |  make dev"

check-deps: ## Verify Docker, uv, Ollama and the three required models
	@command -v uv >/dev/null || { echo "ERROR: uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
	@test -x "$(DOCKER)" || command -v "$(DOCKER)" >/dev/null || { echo "ERROR: docker not found. Install Docker Desktop or Docker Engine."; exit 1; }
	@$(DOCKER) info >/dev/null 2>&1 || { echo "ERROR: Docker daemon not running. Start Docker Desktop (macOS: open -a Docker)."; exit 1; }
	@curl -sf http://localhost:11434/api/tags >/dev/null || { echo "ERROR: Ollama not reachable on :11434. Install from ollama.com and run 'ollama serve'."; exit 1; }
	@for m in qwen2.5:1.5b qwen2.5:3b nomic-embed-text; do \
	  curl -sf http://localhost:11434/api/tags | grep -q "\"$$m" || { echo "ERROR: model $$m missing. Run: ollama pull $$m"; exit 1; }; \
	done
	@echo "deps ok: docker, uv, ollama + qwen2.5:1.5b, qwen2.5:3b, nomic-embed-text"

db: ## Start Postgres + pgvector (docker compose) and wait until healthy
	@$(COMPOSE) up -d --wait postgres

install: ## Install pinned Python dependencies into .venv
	@uv sync --frozen --group dev -q

migrate: install ## Apply migrations (schema, RLS, app role, checkpoint tables)
	@$(RELAY) migrate

seed: install ## Embed and load both tenants' KBs (idempotent)
	@$(RELAY) seed

run: install ## Process an inbox: make run INBOX=path/to/tickets.jsonl
	@$(RELAY) run --inbox $(INBOX)

worker: install ## Long-running worker (reclaims expired leases, reconciles approvals)
	@$(RELAY) worker

approve: install ## List approvals, or approve one: make approve ID=apr-... [BY=name]
	@if [ -n "$(ID)" ]; then $(RELAY) approvals approve $(ID) --by "$(BY)"; else $(RELAY) approvals list -v; fi

reject: install ## Reject with a reason: make reject ID=apr-... REASON='...' [BY=name]
	@test -n "$(ID)" -a -n "$(REASON)" || { echo "usage: make reject ID=apr-... REASON='why'"; exit 1; }
	@$(RELAY) approvals reject $(ID) --reason "$(REASON)" --by "$(BY)"

cases: install ## List escalation and dead-letter cases
	@$(RELAY) cases

eval: install ## Golden-set evaluation: make eval RUNS=5
	@$(RELAY) eval --runs $(RUNS)

test: db install ## Test suite (needs Postgres, not Ollama)
	@uv run --frozen --group dev pytest -o addopts="" -q

api: install ## Start the API on :8077
	@uv run --frozen uvicorn relay.api:app --host 127.0.0.1 --port $${RELAY_API_PORT:-8077}

frontend: ## Start the web console on :5173 (proxies /api to :8077)
	@cd frontend && (test -d node_modules || npm ci --no-audit --no-fund) && npm run dev

dev: db migrate ## API + web console together (Ctrl-C stops both)
	@trap 'kill 0' EXIT; $(MAKE) -s api & $(MAKE) -s frontend & wait

health: install ## Health checks (Postgres, pgvector, Ollama, models, queue, checkpoints)
	@$(RELAY) health

report: install ## Internal report, or client view: make report VIEW=client TENANT=harbor
	@$(RELAY) report --view $${VIEW:-internal} $(if $(TENANT),--tenant $(TENANT),)

export: install ## Re-export out/decisions.jsonl and out/outbox.jsonl from the database
	@$(RELAY) export

down: ## Stop Postgres (keeps data)
	@$(COMPOSE) down

clean: ## Stop Postgres and DELETE its data volume
	@$(COMPOSE) down -v
