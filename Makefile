# pcb_ai_designer_v2 — cibles principales
# make help : liste complète

SHELL := /bin/bash
PY := python3
SERVICES := parser ai_engine simulator router drc_dfm_engine firmware_bridge exporter

.PHONY: help proto lint test benchmark start-services compose-up compose-down clean

help: ## Liste les cibles disponibles
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

proto: ## Génère les stubs gRPC (proto/ -> backend/proto_gen/)
	$(PY) -m grpc_tools.protoc \
		-I proto \
		--python_out=backend/proto_gen \
		--grpc_python_out=backend/proto_gen \
		--mypy_out=backend/proto_gen \
		proto/common/v1/*.proto proto/*/v1/*.proto 2>/dev/null || \
	$(PY) -m grpc_tools.protoc \
		-I proto \
		--python_out=backend/proto_gen \
		--grpc_python_out=backend/proto_gen \
		proto/common/v1/*.proto proto/*/v1/*.proto
	@touch backend/proto_gen/__init__.py backend/proto_gen/**/__init__.py
	@echo "stubs générés dans backend/proto_gen/"

start-services: ## Démarre la gateway + les 7 services gRPC en arrière-plan
	@mkdir -p /tmp/pcb_logs
	@for svc in $(SERVICES); do \
		SERVICE_NAME=$$svc GRPC_PORT=$$($(PY) scripts/alloc_port.py $$svc) \
		nohup $(PY) -m backend.services.$$svc.main > /tmp/pcb_logs/$$svc.log 2>&1 & \
		echo "  $$svc -> /tmp/pcb_logs/$$svc.log"; done
	SERVICE_NAME=api_gateway nohup uvicorn backend.api_gateway.main:app --port 8000 \
		> /tmp/pcb_logs/api_gateway.log 2>&1 &
	@echo "  api_gateway -> http://localhost:8000 (docs: /docs)"

lint: ## Ruff + vérification syntaxe de tous les Python
	$(PY) -m compileall -q backend common tests scripts
	@command -v ruff >/dev/null && ruff check backend common || echo "(ruff non installé — compileall seul)"

test: ## Tests unitaires rapides (socle commun + chaîne déterministe)
	$(PY) -m pytest tests/unit -q

benchmark: ## Harnais de comparaison vs Quilter (section 07)
	$(PY) -m tests.vs_quilter_benchmark.run_benchmark --corpus tests/vs_quilter_benchmark/corpus --report out/benchmark

compose-up: ## Monte la pile de persistance (Redis, Neo4j, MinIO, Postgres) + monitoring
	docker compose up -d

compose-down: ## Arrête et retire la pile docker compose
	docker compose down -v

clean: ## Nettoie caches Python et artefacts
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; rm -rf backend/proto_gen .pytest_cache out
