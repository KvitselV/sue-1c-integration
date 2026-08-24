#!/usr/bin/env bash
# Демонстрационный запуск «с нуля»: окружение, миграции, данные, сервер.
# Каждый шаг доступен и отдельно: make install | migrate | seed | run
set -euo pipefail

cd "$(dirname "$0")/.."

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip --quiet
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install -e . --no-deps

if [ "${REGENERATE:-0}" = "1" ]; then
    .venv/bin/python -m sue.datagen --out data/fixtures
fi

.venv/bin/alembic upgrade head
.venv/bin/python scripts/seed.py

echo "Интерфейс:        http://${HOST}:${PORT}/"
echo "Документация API: http://${HOST}:${PORT}/docs"
exec .venv/bin/uvicorn sue.main:app --host "$HOST" --port "$PORT"
