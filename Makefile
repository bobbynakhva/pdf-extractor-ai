# Quick targets for running the app on your own machine.
#
#   make setup    # one-time: copy .env, build the image
#   make run      # start the app at http://localhost:8000
#   make tunnel   # expose localhost:8000 to the public internet via Cloudflare
#   make logs     # tail container logs
#   make stop     # stop the container
#   make restart  # stop + run
#   make rebuild  # stop + rebuild image + run (use after pulling new code)
#   make clean    # stop + remove container + image
#
# Requires: Docker Desktop (macOS/Windows) or Docker Engine (Linux).
# Optional: cloudflared for `make tunnel` — https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/

.PHONY: help setup run tunnel logs stop restart rebuild clean

help:
	@awk 'BEGIN{FS=":.*##"} /^[a-zA-Z0-9_-]+:.*?##/ {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Copy .env.example -> .env (edit to add API keys) and build the image
	@test -f .env || cp .env.example .env
	@echo "Edit .env if you want to override defaults (all optional)."
	docker compose build

run: ## Start the app. Open http://localhost:8000 afterward.
	@test -f .env || cp .env.example .env
	docker compose up -d
	@echo ""
	@echo "  App running at  http://localhost:8000"
	@echo "  Health check    http://localhost:8000/healthz"
	@echo "  API docs        http://localhost:8000/docs"
	@echo ""

tunnel: ## Expose localhost:8000 to the internet via Cloudflare Tunnel (free).
	@command -v cloudflared >/dev/null 2>&1 || { \
	  echo "cloudflared not found. Install it:"; \
	  echo "  macOS:   brew install cloudflared"; \
	  echo "  Linux:   https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"; \
	  exit 1; \
	}
	cloudflared tunnel --url http://localhost:8000

logs: ## Tail container logs.
	docker compose logs -f pdf-extractor

stop: ## Stop and remove the container (image kept).
	docker compose down

restart: stop run ## Stop + run.

rebuild: ## Rebuild the image after pulling new code, then restart.
	docker compose down
	docker compose build --pull
	docker compose up -d

clean: ## Stop + remove container + remove image.
	docker compose down --rmi local -v
