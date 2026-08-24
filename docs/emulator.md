# Эмулятор выгрузки 1С

Учебный HTTP-сервис, который отдаёт пакеты обмена **того же контракта 2.0**, что
ожидает СУЭ. Это **не** подключение к действующей информационной базе 1С:
пакеты синтетические, в ответе всегда `live_1c: false`.

Зачем он нужен: проверить цепочку «источник по HTTP → адаптер → ETL → сверка»
без лицензии 1С и без живой ИБ.

## Как запустить

Встроен в приложение (пока `SUE_ENABLE_EMULATOR=true`, по умолчанию в local/test):

| Метод | Адрес |
|-------|--------|
| Проверка связи | `GET /emulator/1c/hs/exchange/ping` |
| Описание | `GET /emulator/1c/hs/exchange/info` |
| Список пакетов | `GET /emulator/1c/hs/exchange/batches?scenario=accounting` |
| Один пакет | `GET /emulator/1c/hs/exchange/batches/{exchangeId}` |

Отдельный процесс (удобно «подключить снаружи»):

```powershell
python -m sue.emulator --port 8001
```

Проверка: http://127.0.0.1:8001/hs/exchange/ping

## Как подключить СУЭ

На странице «Загрузка» — блок «Эмулятор выгрузки 1С»: выбрать сценарий и пакет,
нажать «Забрать выгрузку». Пустой адрес — встроенный эмулятор этого же приложения.
Адрес `http://127.0.0.1:8001` — отдельный процесс.

Программный интерфейс:

```http
POST /api/etl/pull
{"url": "http://127.0.0.1:8001", "scenario": "short_history", "exchange_id": null}
```

Регламентный обмен:

```powershell
python -m sue.jobs.exchange --url http://127.0.0.1:8001 --scenario accounting --exchange-id EX-ACCOUNTING-018
```

Разрешены только хосты из `SUE_EMULATOR_ALLOWED_HOSTS` (по умолчанию localhost).
В промышленном контуре эмулятор выключают: `SUE_ENABLE_EMULATOR=false`.
