# ==============================================================================
# AI Data Control Plane — Developer Commands
# ==============================================================================

.PHONY: help up down logs test coverage lint demo demo-bad demo-docs search rollback status clean flows

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------ lifecycle
up: ## Start the full platform (Kestra, Postgres, MinIO, Qdrant, Redis, Gateway, Prometheus, Grafana)
	docker compose up -d --build
	@echo ""
	@echo "  Kestra UI    -> http://localhost:8080"
	@echo "  Gateway API  -> http://localhost:8000/docs"
	@echo "  MinIO UI     -> http://localhost:9001  (minioadmin/minioadmin)"
	@echo "  Qdrant UI    -> http://localhost:6333/dashboard"
	@echo "  Prometheus   -> http://localhost:9090"
	@echo "  Grafana      -> http://localhost:3000  (admin/admin)"

down: ## Stop everything
	docker compose down

clean: ## Stop everything and delete all data volumes
	docker compose down -v

logs: ## Tail all service logs
	docker compose logs -f --tail=100

flows: ## Upload all Kestra flows via the API
	@for f in $$(find flows -name '*.yaml'); do \
		echo "uploading $$f"; \
		curl -s -X POST http://localhost:8080/api/v1/flows/import \
			-F fileUpload=@$$f > /dev/null || true; \
	done
	@echo "flows uploaded — open http://localhost:8080"

# ----------------------------------------------------------------------- dev
test: ## Run unit tests
	pytest tests/ -v

coverage: ## Run tests with coverage report
	pytest tests/ --cov=controlplane --cov-report=term-missing

lint: ## Lint with ruff
	ruff check controlplane/ services/ tests/

# ---------------------------------------------------------------------- demo
demo: ## Ingest the clean product dataset (should PASS gates and promote)
	curl -s -X POST http://localhost:8000/ingest/upload \
		-F dataset=products \
		-F file=@sample_data/products_good.json | python3 -m json.tool

demo-bad: ## Ingest the corrupted dataset (should FAIL gates and be rejected)
	curl -s -X POST http://localhost:8000/ingest/upload \
		-F dataset=products \
		-F file=@sample_data/products_bad.json | python3 -m json.tool

demo-docs: ## Ingest the documents dataset via webhook
	curl -s -X POST http://localhost:8000/ingest/webhook \
		-H 'Content-Type: application/json' \
		-d @sample_data/webhook_documents.json | python3 -m json.tool

search: ## Semantic search against the promoted production data
	curl -s "http://localhost:8000/search/products?q=wireless+headphones" | python3 -m json.tool

rollback: ## Roll production back to the previous promoted version
	curl -s -X POST "http://localhost:8000/rollback/products?reason=demo+rollback" | python3 -m json.tool

status: ## Show recent versions + promotions
	@echo "--- versions ---"
	@curl -s "http://localhost:8000/versions?limit=10" | python3 -m json.tool
	@echo "--- promotions ---"
	@curl -s "http://localhost:8000/promotions?limit=10" | python3 -m json.tool
