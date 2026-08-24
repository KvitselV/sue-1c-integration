"""Русские подписи для интерфейса. Ключи API остаются латиницей и не переводятся."""

from __future__ import annotations

SOURCE_RU = {
    "accounting": "учёт",
    "modeled": "модель",
    "derived": "расчёт",
    "accounting+modeled": "учёт+модель",
}

STATUS_RU = {
    "success": "успех",
    # «сбой», а не «ошибка»: подпись статуса прогона не должна совпадать с уровнем
    # отдельной записи журнала (SEVERITY_RU), иначе на странице загрузок неясно,
    # о чём идёт речь.
    "failed": "сбой",
    "partial": "частично",
    "started": "запущен",
}

ETL_STAGE_RU = {
    "validate_schema": "проверка структуры",
    "validate_rules": "проверка правил",
    "validate": "валидация",
    "load": "загрузка",
    "transform": "преобразование",
    "audit": "аудит",
}

SEVERITY_RU = {
    "error": "ошибка",
    "warning": "предупреждение",
    "info": "сведения",
}

DOC_TYPE_RU = {
    "sale": "реализация",
    "return": "возврат",
}

MARGIN_STATUS_RU = {
    "ok": "норма",
    "warn": "внимание",
    "bad": "ниже нормы",
}


def _lookup(mapping: dict[str, str], value: str | None) -> str:
    if not value:
        return "—"
    return mapping.get(value, value)


def ru_source(value: str | None) -> str:
    return _lookup(SOURCE_RU, value)


def ru_status(value: str | None) -> str:
    return _lookup(STATUS_RU, value)


def ru_stage(value: str | None) -> str:
    return _lookup(ETL_STAGE_RU, value)


def ru_severity(value: str | None) -> str:
    return _lookup(SEVERITY_RU, value)


def ru_doc_type(value: str | None) -> str:
    return _lookup(DOC_TYPE_RU, value)


def ru_margin_status(value: str | None) -> str:
    return _lookup(MARGIN_STATUS_RU, value)
