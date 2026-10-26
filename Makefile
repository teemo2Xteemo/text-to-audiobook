.DEFAULT_GOAL := help

# Môi trường và công cụ thực thi
VENV ?= backend/.venv
PYTHON ?= $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)
PYTEST ?= $(if $(wildcard $(VENV)/bin/pytest),$(VENV)/bin/pytest,pytest)
RUFF ?= $(if $(wildcard $(VENV)/bin/ruff),$(VENV)/bin/ruff,ruff)
RUFF_CACHE_DIR ?= /tmp/.ruff_cache
NPM ?= npm
DOCKER_COMPOSE ?= docker compose

.PHONY: help setup up up-build down restart logs build dev-hybrid \
	test-backend test-frontend test lint-backend format-backend typecheck-frontend lint check clean

help: ## Hiển thị hướng dẫn và danh sách các lệnh
	@echo "Sử dụng: make [lệnh]"
	@echo ""
	@echo "Các lệnh có sẵn:"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# setup calls the venv interpreter by path. GNU Make expands a recipe
# before any line runs, so $(wildcard) cannot see a venv this target
# just created and would install into system Python (PEP 668).

setup: ## Chuẩn bị môi trường ban đầu (.env, backend venv, frontend node_modules)
	@if [ ! -f .env ]; then cp -n .env.example .env && echo "Da khoi tao .env tu .env.example"; else echo ".env da ton tai"; fi
	@if [ ! -d $(VENV) ]; then python3 -m venv $(VENV); fi
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/python -m pip install -e "backend[dev]"
	cd frontend && $(NPM) ci

up: ## Khởi động toàn bộ stack dịch vụ ngầm (Docker Compose)
	@if [ ! -f .env ]; then cp -n .env.example .env && echo "Da khoi tao .env tu .env.example"; fi
	$(DOCKER_COMPOSE) up -d

up-build: ## Build lại và khởi động toàn bộ stack (Docker Compose)
	@if [ ! -f .env ]; then cp -n .env.example .env && echo "Da khoi tao .env tu .env.example"; fi
	$(DOCKER_COMPOSE) up -d --build

down: ## Dừng và gỡ bỏ toàn bộ container của stack
	$(DOCKER_COMPOSE) down

restart: ## Khởi động lại toàn bộ container của stack
	$(DOCKER_COMPOSE) restart

logs: ## Xem log các container theo thời gian thực
	$(DOCKER_COMPOSE) logs -f

build: ## Build lại các Docker images cho dự án
	$(DOCKER_COMPOSE) build

dev-hybrid: ## Rebuild image backend (redis, api, worker) rồi chạy Vite trên host
	@if [ ! -f .env ]; then cp -n .env.example .env && echo "Da khoi tao .env tu .env.example"; fi
	$(DOCKER_COMPOSE) up -d --build redis api worker
	cd frontend && $(NPM) run dev

test-backend: ## Chạy unit test backend với pytest
	$(PYTEST) -m "not integration"

test-frontend: ## Chạy unit test frontend với vitest
	cd frontend && $(NPM) test

test: test-backend test-frontend ## Chạy toàn bộ unit test (backend + frontend)

lint-backend: ## Kiểm tra linter và định dạng mã nguồn backend với ruff
	$(RUFF) check backend --cache-dir $(RUFF_CACHE_DIR)
	$(RUFF) format --check backend --cache-dir $(RUFF_CACHE_DIR)

format-backend: ## Tự động format mã nguồn backend với ruff
	$(RUFF) format backend --cache-dir $(RUFF_CACHE_DIR)

typecheck-frontend: ## Kiểm tra kiểu TypeScript trong frontend
	cd frontend && $(NPM) run typecheck

lint: lint-backend typecheck-frontend ## Kiểm tra toàn bộ mã nguồn (backend lint + frontend typecheck)

check: lint test ## Kiểm tra toàn diện trước khi commit (lint, test, hooks, scan)
	python3 .cursor/hooks/selftest.py
	python3 .github/scripts/scan_repo.py

clean: ## Dọn cache host; cache root của Docker thì in lệnh gỡ
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@failed=0; \
	for path in .pytest_cache backend/.pytest_cache .ruff_cache backend/.ruff_cache .coverage htmlcov; do \
	  if [ -e "$$path" ] && ! rm -rf "$$path" 2>/dev/null; then \
	    echo "make clean: permission denied: $$path"; \
	    failed=1; \
	  fi; \
	done; \
	if [ "$$failed" -ne 0 ]; then \
	  echo "These paths are owned by another user (often Docker). Remove them with:"; \
	  echo "  docker run --rm -v \"$$(pwd):/work\" -w /work alpine rm -rf .pytest_cache backend/.pytest_cache .ruff_cache backend/.ruff_cache .coverage htmlcov"; \
	  exit 1; \
	fi
