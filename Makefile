.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck test test-pg test-core coverage migrate gen-data seed run docker-up docker-down clean

PY ?= python
VENV := .venv
BIN := $(VENV)/bin
HOST ?= 127.0.0.1
PORT ?= 8000

help: ## Список команд
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Виртуальное окружение и зависимости (с dev-инструментами)
	$(PY) -m venv $(VENV)
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/pip install -r requirements-dev.txt
	$(BIN)/pip install -e . --no-deps
	$(BIN)/pre-commit install

lint: ## Ruff (проверка) + формат-чек
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format: ## Автоисправление и форматирование
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

typecheck: ## mypy
	$(BIN)/mypy

test: ## Тесты на SQLite
	$(BIN)/pytest

test-pg: ## Тесты на PostgreSQL (нужен SUE_TEST_PG_URL)
	$(BIN)/pytest -m pg -p no:randomly

test-core: ## Порог покрытия ядра предметной логики
	$(BIN)/pytest -q --cov=sue.domain --cov=sue.etl --cov=sue.adapter_1c --cov=sue.money \
		--cov-report=term-missing --cov-fail-under=85

coverage: ## Покрытие с HTML-отчётом
	$(BIN)/pytest --cov --cov-report=term-missing --cov-report=html
	@echo "Отчёт: htmlcov/index.html"

migrate: ## Применить миграции
	$(BIN)/alembic upgrade head

gen-data: ## Сгенерировать сценарии фикстур
	$(BIN)/python -m sue.datagen --out data/fixtures

seed: migrate ## Загрузить демонстрационные данные штатным ETL
	$(BIN)/python scripts/seed.py

run: ## Запустить приложение
	$(BIN)/uvicorn sue.main:app --host $(HOST) --port $(PORT) --reload

docker-up: ## PostgreSQL + приложение в Docker
	docker compose up --build -d
	docker compose exec -T api alembic upgrade head
	docker compose exec -T api python scripts/seed.py

docker-down: ## Остановить контейнеры и удалить томы
	docker compose down -v

clean: ## Удалить артефакты сборки и кэши
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
