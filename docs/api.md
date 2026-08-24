# HTTP-API

Базовый адрес: `http://127.0.0.1:8000/api`. Интерактивная документация — `/docs`,
машиночитаемая схема — `/openapi.json`. У каждого эндпоинта объявлена модель ответа,
поэтому схема OpenAPI соответствует фактическим полям.

Все примеры ниже — реальные ответы приложения на демонстрационном наборе
`accounting` (6 торговых точек, до 77 недель истории, 2450 документов, 8637 строк).

## Общие соглашения

| Правило | Как реализовано |
|---------|-----------------|
| Кодировка | UTF-8, `application/json` |
| Денежные величины | число с двумя знаками; в БД хранятся в копейках, поэтому суммы сходятся точно |
| Количества | число с тремя знаками |
| Отметки времени | ISO 8601 в UTC с суффиксом `Z` |
| Период | `date_from` и `date_to` включительно; `date_from > date_to` → `422` |
| Пагинация | `limit` (1…500, по умолчанию 50) и `offset` (≥ 0) |
| Ошибки | единый формат `{"error": {"code", "message", "details"}}` |
| Происхождение | каждый показатель — объект с полями `value`, `source`, `note`, `unit` |
| Доступ | при заданном `SUE_API_KEY` загрузка данных требует заголовок `X-API-Key` |

### Контроль доступа

Барьер выключен по умолчанию: запуск демонстрации не должен требовать секретов.
Если задан `SUE_API_KEY`, изменяющие операции (`POST /api/etl/upload`, `POST /api/etl/import`,
`POST /api/etl/pull`) без корректного заголовка `X-API-Key` возвращают `401` с кодом
`unauthorized`.
Дополнительно `SUE_PROTECT_READ=true` закрывает тем же ключом и операции чтения.

```bash
curl -s -X POST http://127.0.0.1:8000/api/etl/upload \
     -H "X-API-Key: $SUE_API_KEY" -F "file=@пакет.json"
```

Живость, готовность и версия (`/api/health*`, `/api/version`) остаются открытыми при любых
настройках: пробы оркестратора и проверка развёртывания не должны знать ключа. Веб-интерфейс
барьером не закрывается — в промышленном контуре его закрывают обратным прокси или внешней
аутентификацией.

### Формат ошибки

```json
{
  "error": {
    "code": "no_data",
    "message": "Нет данных по торговой точке за указанный период",
    "details": { "store_id": 999 }
  }
}
```

| Код | HTTP | Когда возникает |
|-----|------|-----------------|
| `invalid_period` | 422 | `date_from` позже `date_to` |
| `unauthorized` | 401 | отсутствует или неверен `X-API-Key` при включённом барьере |
| `not_found` | 404 | нет объекта с указанным идентификатором |
| `no_data` | 404 | объект есть, но за период нет наблюдений |
| `unsafe_path` | 400 | путь выходит за пределы каталога фикстур |
| `unsafe_url` | 400 | адрес эмулятора не http(s) или хост не в белом списке |
| `emulator_not_found` | 404 | сценарий или пакет эмулятора не найден |
| `emulator_disabled` | 404 | встроенный эмулятор выключен (`SUE_ENABLE_EMULATOR`) |
| `path_not_found` | 404 | путь внутри каталога фикстур не существует |
| `invalid_source` | 400 | файл не в UTF-8, не JSON или корень не объект |
| `empty_file` | 400 | загружен пустой файл |
| `file_too_large` | 413 | размер превышает `SUE_MAX_UPLOAD_BYTES` |
| `local_import_disabled` | 403 | `SUE_ALLOW_LOCAL_PATH_IMPORT=false` |
| `validation_error` | 422 | тело или параметры запроса не соответствуют схеме |
| `internal_error` | 500 | необработанное исключение (детали — в журнале по `request_id`) |

---

## Служебные

### `GET /api/version`

```bash
curl -s http://127.0.0.1:8000/api/version
```

```json
{
  "name": "sue",
  "version": "1.2.0",
  "contract_version": "2.0",
  "app_env": "local",
  "database": "sqlite"
}
```

`contract_version` — версия контракта обмена, которую принимает приёмник. Пакет
с другой версией в `meta.contractVersion` отклоняется.

### `GET /api/health/live` и `GET /api/health/ready`

`live` подтверждает работу процесса и не обращается к БД. `ready` выполняет
`SELECT 1`; при недоступной БД возвращает `503` со `status: "degraded"` — именно
этот адрес использует `HEALTHCHECK` контейнера.

```json
{ "status": "ok", "checks": { "database": "ok" } }
```

`GET /api/health` — синоним `live`, оставлен для совместимости.

---

## Справочники

### `GET /api/stores`

Параметры: `limit`, `offset`.

```bash
curl -s "http://127.0.0.1:8000/api/stores?limit=2"
```

```json
{
  "items": [
    {
      "id": 1,
      "source_ref": "store-01",
      "code": "TT-01",
      "name": "ТТ Центральная",
      "city": "Казань",
      "store_format": "супермаркет",
      "is_active": true
    },
    {
      "id": 2,
      "source_ref": "store-02",
      "code": "TT-02",
      "name": "ТТ Заречная",
      "city": "Казань",
      "store_format": "у дома",
      "is_active": true
    }
  ],
  "total": 6,
  "limit": 2,
  "offset": 0
}
```

`source_ref` — идентификатор объекта в системе-источнике (аналог GUID ссылки 1С);
он же служит ключом идемпотентности при повторной загрузке пакета.

---

## Рентабельность

### `GET /api/profitability`

Показатели по всем торговым точкам. Параметры: `date_from`, `date_to`.
Выполняется одной агрегацией `GROUP BY`, а не запросом на каждую точку.

```bash
curl -s "http://127.0.0.1:8000/api/profitability?date_from=2026-04-01&date_to=2026-04-30"
```

```json
[
  {
    "store_id": 1,
    "store_code": "TT-01",
    "store_name": "ТТ Центральная",
    "city": "Казань",
    "period_from": "2026-04-01",
    "period_to": "2026-04-30",
    "overhead_rate": 12.0,
    "lines": 98,
    "lines_without_cost": 19,
    "cost_accounting_share_pct": 80.54,
    "cost_modeled_share_pct": 19.46,
    "revenue": {
      "value": 2212498.45,
      "source": "accounting",
      "note": "Сумма реализации за вычетом возвратов",
      "unit": "RUB"
    },
    "gross_revenue": {
      "value": 2219625.45,
      "source": "accounting",
      "note": "Сумма amount по документам реализации",
      "unit": "RUB"
    },
    "returns": {
      "value": 7127.0,
      "source": "accounting",
      "note": "Сумма по документам возврата от покупателя",
      "unit": "RUB"
    },
    "cost": {
      "value": 1312430.25,
      "source": "accounting+modeled",
      "note": "Себестоимость: 79 строк из учёта (costAmount), 19 строк смоделировано по доле категории",
      "unit": "RUB"
    },
    "gross_profit": {
      "value": 900068.2,
      "source": "derived",
      "note": "Выручка − себестоимость",
      "unit": "RUB"
    },
    "overhead": {
      "value": 265499.81,
      "source": "modeled",
      "note": "Аллокация накладных расходов: выручка × 12.00%",
      "unit": "RUB"
    },
    "operating_profit": {
      "value": 634568.39,
      "source": "derived",
      "note": "Валовая прибыль − накладные расходы",
      "unit": "RUB"
    },
    "operating_margin_pct": {
      "value": 28.68,
      "source": "derived",
      "note": "Операционная прибыль / выручка × 100",
      "unit": "%"
    }
  }
]
```

Пример сокращён; полный набор показателей в каждом элементе: `revenue`,
`gross_revenue`, `returns`, `cost`, `cost_accounting`, `cost_modeled`, `gross_profit`,
`gross_margin_pct`, `overhead`, `operating_profit`, `operating_margin_pct`, `quantity`.
Пара `gross_revenue` и `returns` показывает, насколько возвраты уменьшили выручку.

### `GET /api/profitability/{store_id}`

Показатели одной точки. Дополнительный параметр `sensitivity_delta`
(от −0.5 до 0.5) сдвигает ставку накладных расходов — для анализа
чувствительности операционной рентабельности.

```bash
curl -s "http://127.0.0.1:8000/api/profitability/1?sensitivity_delta=0.03"
```

Если за период нет наблюдений — `404` с кодом `no_data`.

### `GET /api/profitability/{store_id}/categories`

Разрез по товарным категориям.

```json
[
  {
    "category": "Молочная продукция",
    "revenue": 7110342.43,
    "cost": 5174902.25,
    "gross_profit": 1935440.18,
    "gross_margin_pct": 27.22,
    "lines": 409
  },
  {
    "category": "Напитки",
    "revenue": 6893067.8,
    "cost": 2973952.05,
    "gross_profit": 3919115.75,
    "gross_margin_pct": 56.86,
    "lines": 385
  }
]
```

### `GET /api/profitability.csv`

Та же таблица в CSV: разделитель `;`, кодировка UTF-8 с BOM, заголовки на русском —
файл открывается в Excel без настройки импорта. Принимает те же `date_from` и `date_to`.

```bash
curl -s -OJ "http://127.0.0.1:8000/api/profitability.csv?date_from=2026-01-01&date_to=2026-05-31"
```

### `GET /api/profitability/report.pdf` и `GET /api/profitability/{id}/report.pdf`

Печатный отчёт по сети или по одной торговой точке. Те же показатели, что в интерфейсе,
с меткой происхождения. В колонтитуле указано, что данные синтетические.
В интерфейсе генерация собрана на `/ui/reports`.

```bash
curl -s -OJ "http://127.0.0.1:8000/api/profitability/1/report.pdf"
curl -s -OJ "http://127.0.0.1:8000/api/profitability/report.pdf?date_from=2026-01-01&date_to=2026-05-31"
```

### `GET /api/profitability/compare`

Сравнение двух периодов по точкам. Обязательные параметры: `base_from`, `base_to`,
`compare_from`, `compare_to`.

```bash
curl -s "http://127.0.0.1:8000/api/profitability/compare\
?base_from=2026-03-01&base_to=2026-03-31&compare_from=2026-04-01&compare_to=2026-04-30"
```

```json
[
  {
    "store_id": 1,
    "store_code": "TT-01",
    "store_name": "ТТ Центральная",
    "has_base": true,
    "has_compare": true,
    "base_period": { "from": "2026-03-01", "to": "2026-03-31" },
    "compare_period": { "from": "2026-04-01", "to": "2026-04-30" },
    "metrics": {
      "revenue": { "base": 2247184.52, "compare": 2212498.45, "absolute": -34686.07, "relative_pct": -1.54 },
      "cost": { "base": 1353494.83, "compare": 1312430.25, "absolute": -41064.58, "relative_pct": -3.03 },
      "gross_profit": { "base": 893689.69, "compare": 900068.2, "absolute": 6378.51, "relative_pct": 0.71 },
      "operating_profit": { "base": 624027.55, "compare": 634568.39, "absolute": 10540.84, "relative_pct": 1.69 }
    },
    "operating_margin_pct": { "base": 27.77, "compare": 28.68, "absolute_pp": 0.91 },
    "margin_status": "ok"
  }
]
```

Изменение рентабельности выражено в процентных пунктах (`absolute_pp`), а не в процентах
от процента. Если в базовом периоде показатель равен нулю, `relative_pct` равен `null`:
рост «в разы от нуля» не определён. Точка, у которой в одном из периодов нет документов,
из выдачи не исчезает — она помечается `has_base` или `has_compare`, потому что открытие
и закрытие точки как раз и требуют внимания.

---

## Витрина для внешней отчётности

### `GET /api/mart/kpi`

Плоская таблица показателей: одна строка — торговая точка за период, все величины
скалярами. Нужна для передачи в систему отчётности, которая не разбирает вложенные объекты
происхождения. Параметры: `date_from`, `date_to`.

```json
[
  {
    "store_id": 1,
    "store_code": "TT-01",
    "store_name": "ТТ Центральная",
    "city": "Казань",
    "period_from": "2024-12-09",
    "period_to": "2026-05-30",
    "gross_revenue": 35822645.55,
    "returns": 189211.0,
    "revenue": 35633434.55,
    "quantity": 483301.95,
    "cost": 21326625.14,
    "cost_accounting": 17002538.17,
    "cost_modeled": 4324086.97,
    "gross_profit": 14306809.41,
    "gross_margin_pct": 40.15,
    "overhead": 4276012.15,
    "overhead_rate_pct": 12.0,
    "operating_profit": 10030797.26,
    "operating_margin_pct": 28.15,
    "cost_accounting_share_pct": 79.72,
    "cost_modeled_share_pct": 20.28,
    "lines": 1977,
    "lines_without_cost": 399,
    "cost_source": "accounting+modeled",
    "margin_status": "warn"
  }
]
```

Условность величин при переходе к плоской форме не теряется: `cost_source` и доли
`cost_accounting_share_pct` / `cost_modeled_share_pct` показывают, какая часть
себестоимости взята из учёта, а какая смоделирована.

`margin_status` — оценка операционной рентабельности по управленческим порогам
`SUE_MARGIN_TARGET_PCT` и `SUE_MARGIN_WARN_PCT`: `ok` — не ниже целевого значения,
`warn` — между порогами, `bad` — ниже порога предупреждения. Пороги задаются настройкой
и учётной величиной не являются.

### `GET /api/mart/kpi.csv`

Та же витрина файлом: разделитель `;`, UTF-8 с BOM, заголовки на русском — путь загрузки
в отчётность, у которой нет доступа к API.

```bash
curl -s -OJ "http://127.0.0.1:8000/api/mart/kpi.csv?date_from=2026-01-01&date_to=2026-05-31"
```

---

## Загрузка данных

### `POST /api/etl/upload`

Основной путь передачи данных: файл пакета обмена загружается как `multipart/form-data`.

```bash
curl -s -X POST http://127.0.0.1:8000/api/etl/upload \
     -F "file=@data/fixtures/accounting/exchange_accounting_001.json"
```

Параметр `dry_run=true` только проверяет пакет и считает итоги, ничего не сохраняя;
в ответе тогда `"dry_run": true` и соответствующее сообщение.
Ответ — запись журнала загрузки (`201 Created`):

```json
{
  "id": 18,
  "source_system": "1C_Retail_FileExchange",
  "source_file": "accounting/exchange_accounting_018.json",
  "source_hash": "0dd4a95bc3f0a885d41f6415ba8ea5744efa5ff96f0ecf6c4249991d745d0283",
  "exchange_id": "EX-ACCOUNTING-018",
  "contract_version": "2.0",
  "status": "success",
  "dry_run": false,
  "started_at": "2026-08-19T06:49:17.967073Z",
  "finished_at": "2026-08-19T06:49:18.071074Z",
  "duration_ms": 103,
  "stores_upserted": 6,
  "products_upserted": 10,
  "documents_accepted": 187,
  "documents_rejected": 0,
  "lines_accepted": 650,
  "lines_rejected": 0,
  "errors_count": 0,
  "message": "Загрузка выполнена; exchangeId=EX-ACCOUNTING-018; ТТ=6; номенклатура=10; документы=187; строки=650"
}
```

Счётчики раздельные: справочники, документы и строки не смешиваются.
`source_hash` — SHA-256 файла, позволяет отличить повторную загрузку того же
пакета от изменённой выгрузки. `source_file` — имя внутри каталога обмена:
абсолютные пути файловой системы сервера в журнал не записываются.

Ограничения: размер файла — `SUE_MAX_UPLOAD_BYTES` (по умолчанию 32 МБ),
число документов — `SUE_MAX_BATCH_DOCUMENTS`.

### `POST /api/etl/import`

Загрузка пакета, который уже лежит в каталоге фикстур на сервере — удобно
для демонстрации регламентного обмена.

```bash
curl -s -X POST http://127.0.0.1:8000/api/etl/import \
     -H "Content-Type: application/json" \
     -d '{"path": "accounting", "dry_run": true}'
```

`path` — **только имя файла или подкаталога внутри `SUE_FIXTURES_DIR`**. Абсолютные
пути и переходы вверх отклоняются:

```bash
curl -s -X POST http://127.0.0.1:8000/api/etl/import \
     -H "Content-Type: application/json" \
     -d '{"path": "../../../etc/passwd"}'
```

```json
{
  "error": { "code": "unsafe_path", "message": "Переход в родительский каталог запрещён", "details": null }
}
```

Эндпоинт можно полностью отключить: `SUE_ALLOW_LOCAL_PATH_IMPORT=false` → `403`.

### `POST /api/etl/pull`

Забрать пакет(ы) с эмулятора выгрузки 1С и прогнать штатный ETL. Это не живая
информационная база: сервис `/hs/exchange` отдаёт синтетические пакеты контракта 2.0.

```bash
curl -s -X POST http://127.0.0.1:8000/api/etl/pull \
     -H "Content-Type: application/json" \
     -d '{"url": "http://127.0.0.1:8001", "scenario": "accounting"}'
```

Пустой `url` — встроенный эмулятор этого же приложения (`/emulator/1c`).
Разрешены только хосты из `SUE_EMULATOR_ALLOWED_HOSTS`. Подробно: [`emulator.md`](emulator.md).

### `GET /api/etl/runs`

Журнал загрузок. Параметры: `status` (`started`, `success`, `partial`, `failed`),
`limit`, `offset`. Возвращает страницу записей того же вида, что показан выше.

### `GET /api/etl/runs/{run_id}` и `GET /api/etl/runs/{run_id}/errors`

Одна запись журнала и постранично её ошибки:

```json
{
  "items": [
    {
      "id": 12,
      "run_id": 7,
      "stage": "validate_rules",
      "severity": "error",
      "entity": "saleDocument",
      "source_ref": "doc-0042",
      "location": "saleDocuments/41/storeRef",
      "detail": "неизвестная ссылка на магазин: store-99",
      "created_at": "2026-08-11T03:54:25.831000Z"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

Этапы (`stage`): `validate_schema` — нарушение JSON Schema, `validate_rules` —
бизнес-правило, `load` — ошибка сохранения, `audit` — сбой при записи журнала.
Уровни (`severity`): `error` — запись отклонена, `warning` — принята с замечанием.

---

## Сверка

### `GET /api/reconciliation`

Сравнение контрольных сумм файлов обмена с содержимым СУЭ. Параметр `path` —
каталог или файл внутри `SUE_FIXTURES_DIR` (по умолчанию `accounting`).

```bash
curl -s "http://127.0.0.1:8000/api/reconciliation?path=accounting"
```

```json
{
  "source": "accounting",
  "source_totals": {
    "stores": 6, "products": 10, "documents": 2450, "lines": 8637,
    "revenue": 110667694.15, "quantity": 1508022.2, "cost_accounting": 53137553.65
  },
  "db_totals": {
    "stores": 6, "products": 10, "documents": 2450, "lines": 8637,
    "revenue": 110667694.15, "quantity": 1508022.2, "cost_accounting": 53137553.65
  },
  "diff_minor_units": {
    "stores": 0, "products": 0, "documents": 0, "lines": 0,
    "revenue_kopecks": 0, "quantity_milli": 0, "cost_accounting_kopecks": 0
  },
  "matched": true
}
```

Расхождение считается в минимальных единицах (копейки, тысячные доли), поэтому
`matched: true` означает совпадение до копейки, а не «примерно совпало».
То же проверяется автотестами на SQLite и на PostgreSQL.

Поле `source` содержит имя внутри каталога обмена: абсолютные пути файловой системы
сервера в ответах не раскрываются.

---

## Веб-интерфейс

Страницы отдаются тем же приложением и в схему OpenAPI не включены.

| Адрес | Содержание |
|-------|-----------|
| `/` | сводка: выручка, прибыль, рентабельность, состояние загрузок |
| `/ui/profitability` | таблица показателей по точкам с выбором периода |
| `/ui/profitability/{store_id}` | карточка точки: динамика, категории, чувствительность |
| `/ui/compare` | сравнение двух периодов по точкам: графики выручки и маржи, таблица изменений; при пустых параметрах — два соседних окна по данным |
| `/ui/reports` | PDF: свод по рентабельности сети и отчёт по каждой точке |
| `/ui/exchange` | отдельная страница выгрузки в виде книги Excel (нет в меню) |
| `/ui/etl` | журнал загрузок и последние ошибки |
