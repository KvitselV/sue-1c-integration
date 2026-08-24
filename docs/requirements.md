# Требования и их выполнение

Таблица связывает каждое требование с местом реализации и способом проверки.
Требование без проверки считается невыполненным.

## Функциональные требования

| № | Требование | Реализация | Проверка |
|---|-----------|------------|----------|
| Ф1 | Приём данных по версионированному контракту, структурно совместимому с 1С:Розница | `schemas/1c_retail_exchange.schema.json` (2.0), `sue.adapter_1c` | `tests/unit/test_validator.py`, `tests/integration/test_datagen.py` |
| Ф2 | Два способа приёма: загрузка файла и чтение каталога | `POST /api/etl/upload`, `POST /api/etl/import` | `tests/api/test_api.py` |
| Ф3 | Валидация структуры и бизнес-правил с журналом ошибок по записям | `sue.adapter_1c.validator`, таблица `etl_errors` | `tests/unit/test_validator.py`, 16 некорректных пакетов |
| Ф4 | Проверочный прогон без записи данных | параметр `dry_run` | `tests/integration/test_etl.py` |
| Ф5 | Идемпотентная загрузка: повтор не создаёт дублей, изменения применяются | upsert по `source_ref` в `sue.etl.pipeline` | `tests/integration/test_etl.py` |
| Ф6 | Аудит прогонов: статус, счётчики, длительность, хеш пакета | таблица `etl_runs`, страница `/ui/etl` | `tests/integration/test_etl.py`, `tests/ui/test_pages.py` |
| Ф7 | Сверка контрольных сумм «источник ↔ приёмник» | `sue.domain.reconciliation`, `GET /api/reconciliation` | `tests/integration/test_reconciliation.py` |
| Ф8 | Расчёт рентабельности точки с меткой происхождения у каждой величины | `sue.domain.profitability`, `sue.domain.provenance` | `tests/unit/test_profitability.py` |
| Ф9 | Учёт возвратов в выручке и себестоимости | `doc_type`, отрицательный знак строк | `tests/integration/test_etl.py`, `tests/unit/test_profitability.py` |
| Ф10 | Разрез по товарным категориям | `GET /api/profitability/{id}/categories` | `tests/api/test_api.py` |
| Ф11 | Анализ чувствительности к моделируемой ставке накладных | параметр `sensitivity_delta` | `tests/unit/test_profitability.py` |
| Ф14 | Фильтрация показателей по периоду | параметры `date_from`, `date_to` в API и интерфейсе | `tests/api/test_api.py`, `tests/ui/test_pages.py` |
| Ф15 | Выгрузка результатов в CSV | `GET /api/profitability.csv` | `tests/api/test_api.py` |
| Ф16 | Веб-интерфейс на русском языке: сводка, рентабельность, карточка точки, сравнение, отчёты PDF, загрузки, табличный вид пакета выгрузки | `sue.web.templates`, `sue.i18n` | `tests/ui/test_pages.py`, `tests/unit/test_i18n.py` |
| Ф17 | REST API с описанием OpenAPI | `sue.api.routes`, `/docs` | `tests/api/test_api.py` |
| Ф18 | Воспроизводимая генерация данных со сценариями и контрольными суммами | пакет `sue.datagen`, `manifest.json` | `tests/integration/test_datagen.py` |
| Ф19 | Витрина показателей для внешней отчётности: плоские строки и файл CSV | `sue.domain.mart`, `GET /api/mart/kpi`, `GET /api/mart/kpi.csv` | `tests/unit/test_mart.py`, `tests/api/test_mart_api.py` |
| Ф20 | Оценка операционной рентабельности по управленческим порогам | `margin_status`, `SUE_MARGIN_TARGET_PCT`, `SUE_MARGIN_WARN_PCT` | `tests/unit/test_mart.py`, `tests/ui/test_pages.py` |
| Ф21 | Сравнение двух периодов по точкам с изменением в процентных пунктах | `compare_periods`, `GET /api/profitability/compare`, `/ui/compare` | `tests/unit/test_mart.py`, `tests/api/test_mart_api.py`, `tests/ui/test_pages.py` |
| Ф22 | Регламентный обмен вне веб-приложения: загрузка, сверка, отчёт, код возврата | `sue.jobs.exchange` | `tests/integration/test_exchange_job.py` |
| Ф23 | PDF-отчёт по торговой точке и свод по сети | `sue.reports.pdf`, `/ui/reports`, `GET /api/profitability/report.pdf` | `tests/unit/test_pdf_report.py`, `tests/api/test_api.py`, `tests/ui/test_pages.py` |

## Нефункциональные требования

| № | Требование | Реализация | Проверка |
|---|-----------|------------|----------|
| Н1 | Запуск с нуля одной командой | `scripts/run_demo.ps1`, `scripts/run_demo.sh`, `make`, `tasks.ps1` | шаг `smoke` в конвейере CI |
| Н2 | Работа на SQLite без внешних зависимостей и на PostgreSQL | `sue.db`, `docker-compose.yml` | `tests/integration/test_postgres.py` (маркер `pg`) |
| Н3 | Побитово одинаковые результаты на обеих СУБД | хранение денег целым числом копеек | `tests/integration/test_postgres.py` |
| Н4 | Точность денежных величин: итог по всем точкам равен сумме итогов | `sue.money`, агрегация в копейках | `tests/unit/test_money.py`, `tests/unit/test_profitability.py` |
| Н5 | Эволюция схемы миграциями, а не пересозданием базы | Alembic | `tests/integration/test_migrations.py` |
| Н6 | Работа без доступа в интернет | локальные Chart.js, шрифты, изображения | `tests/ui/test_pages.py` |
| Н7 | Защита от выхода за пределы каталога данных при импорте | `sue.api.paths` | `tests/unit/test_paths_and_config.py`, `tests/api/test_api.py` |
| Н8 | Ограничение размера загружаемого файла и числа документов в пакете | `SUE_MAX_UPLOAD_BYTES`, `SUE_MAX_BATCH_DOCUMENTS` | `tests/api/test_api.py` |
| Н9 | Единый формат ошибок API | `sue.api.errors` | `tests/api/test_api.py` |
| Н10 | Пробы живости и готовности для эксплуатации | `/api/health/live`, `/api/health/ready` | `tests/api/test_api.py` |
| Н11 | Структурные логи с идентификатором запроса | `sue.logging_config`, middleware | `SUE_LOG_JSON=true` |
| Н12 | Конфигурация переменными окружения с проверкой значений | `sue.config`, `.env.example` | `tests/unit/test_paths_and_config.py` |
| Н13 | Единая версия в пакете, образе и ответе API | `src/sue/__init__.py` как единственный источник | `GET /api/version`, `tests/api/test_api.py` |
| Н14 | Автоматический контроль качества кода | Ruff, Mypy (строгий режим), pre-commit | конвейер CI |
| Н15 | Тесты как условие приёмки изменений | 4 уровня тестов, порог покрытия | `.github/workflows/ci.yml`, [`testing.md`](testing.md) |
| Н16 | Документированные архитектурные решения | записи ADR | [`adr/`](adr/README.md) |
| Н17 | Барьер доступа на изменяющих операциях, не мешающий пробам оркестратора | `sue.api.security`, `SUE_API_KEY`, `SUE_PROTECT_READ` | `tests/api/test_security.py` |
| Н18 | Обоснование выбора источника данных результатами поиска альтернатив | [`data-sources-review.md`](data-sources-review.md) | [ADR 0005](adr/0005-synthetic-data-generator.md) |

## Границы работы

Подключения к действующей информационной базе нет (Ф1 реализовано файловым обменом,
[ADR 0001](adr/0001-file-exchange-instead-of-live-1c.md)), все данные синтетические
([`data-provenance.md`](data-provenance.md)), контроль доступа ограничен одним общим
ключом на изменяющих операциях (Н17) без ролевой модели и защиты веб-интерфейса.
