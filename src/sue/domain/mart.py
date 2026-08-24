"""Витрина показателей и сравнение периодов.

Основной расчёт (``profitability``) выдаёт каждый показатель вместе с происхождением:
``{value, source, note, unit}``. Это нужно, чтобы условность величины не терялась,
но инструменты отчётности ожидают плоскую таблицу «одна строка — один объект».

Здесь показатели раскладываются в плоские строки, пригодные для передачи во внешний
контур отчётности. Доли учётной и моделируемой себестоимости остаются отдельными
полями: без них принимающая система не отличит учётный факт от оценки.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sue.config import get_settings
from sue.db.models import SaleLine
from sue.domain.profitability import list_store_profitability
from sue.money import q2

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_BAD = "bad"


def margin_status(operating_margin_pct: float | Decimal) -> str:
    """Светофор по операционной рентабельности.

    Пороги — управленческий ориентир, а не учётная величина: они задаются настройкой
    и в выдаче помечаются как моделируемые.
    """
    settings = get_settings()
    value = float(operating_margin_pct)
    if value >= settings.margin_target_pct:
        return STATUS_OK
    if value >= settings.margin_warn_pct:
        return STATUS_WARN
    return STATUS_BAD


def _flat_row(row: dict[str, Any]) -> dict[str, Any]:
    operating_margin = row["operating_margin_pct"]["value"]
    return {
        "store_id": row["store_id"],
        "store_code": row["store_code"],
        "store_name": row["store_name"],
        "city": row["city"],
        "period_from": row["period_from"],
        "period_to": row["period_to"],
        "gross_revenue": row["gross_revenue"]["value"],
        "returns": row["returns"]["value"],
        "revenue": row["revenue"]["value"],
        "quantity": row["quantity"]["value"],
        "cost": row["cost"]["value"],
        "cost_accounting": row["cost_accounting"]["value"],
        "cost_modeled": row["cost_modeled"]["value"],
        "gross_profit": row["gross_profit"]["value"],
        "gross_margin_pct": row["gross_margin_pct"]["value"],
        "overhead": row["overhead"]["value"],
        "overhead_rate_pct": row["overhead_rate"],
        "operating_profit": row["operating_profit"]["value"],
        "operating_margin_pct": operating_margin,
        "cost_accounting_share_pct": row["cost_accounting_share_pct"],
        "cost_modeled_share_pct": row["cost_modeled_share_pct"],
        "lines": row["lines"],
        "lines_without_cost": row["lines_without_cost"],
        "cost_source": row["cost"]["source"],
        "margin_status": margin_status(operating_margin),
    }


def kpi_mart(
    db: Session,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    """Плоская витрина показателей по торговым точкам."""
    return [_flat_row(row) for row in list_store_profitability(db, date_from, date_to)]


MART_COLUMNS: tuple[tuple[str, str], ...] = (
    ("store_code", "Код ТТ"),
    ("store_name", "Торговая точка"),
    ("city", "Город"),
    ("period_from", "Период с"),
    ("period_to", "Период по"),
    ("gross_revenue", "Сумма реализации"),
    ("returns", "Возвраты"),
    ("revenue", "Выручка"),
    ("quantity", "Количество"),
    ("cost", "Себестоимость"),
    ("cost_accounting", "Себестоимость из учёта"),
    ("cost_modeled", "Себестоимость смоделирована"),
    ("gross_profit", "Валовая прибыль"),
    ("gross_margin_pct", "Валовая рентабельность, %"),
    ("overhead", "Накладные расходы"),
    ("overhead_rate_pct", "Ставка накладных, %"),
    ("operating_profit", "Операционная прибыль"),
    ("operating_margin_pct", "Операционная рентабельность, %"),
    ("cost_accounting_share_pct", "Доля учётной себестоимости, %"),
    ("cost_modeled_share_pct", "Доля моделируемой себестоимости, %"),
    ("cost_source", "Происхождение себестоимости"),
    ("margin_status", "Оценка рентабельности"),
    ("lines", "Строк"),
    ("lines_without_cost", "Строк без себестоимости"),
)


def _delta(current: float, previous: float) -> dict[str, float | None]:
    """Изменение показателя: абсолютное и относительное.

    При нулевой базе относительное изменение не определено — возвращается ``None``,
    а не бесконечность или ноль, которые читались бы как «ничего не изменилось».
    """
    absolute = float(q2(Decimal(str(current)) - Decimal(str(previous))))
    if previous == 0:
        return {"absolute": absolute, "relative_pct": None}
    relative = float(q2(Decimal(str(absolute)) / Decimal(str(abs(previous))) * 100))
    return {"absolute": absolute, "relative_pct": relative}


COMPARED_METRICS = (
    "revenue",
    "cost",
    "gross_profit",
    "operating_profit",
)


DEFAULT_COMPARE_WINDOW_DAYS = 28


def default_compare_periods(
    db: Session, window_days: int = DEFAULT_COMPARE_WINDOW_DAYS
) -> tuple[date, date, date, date] | None:
    """Два соседних окна, отсчитанных от последней даты в данных.

    Страница сравнения должна показывать осмысленный результат сразу, без ручного ввода
    четырёх дат. Отсчёт идёт от последней загруженной даты, а не от текущей: данные
    выгрузки историчны, и окно «последний месяц по календарю» на них обычно пусто.
    """
    last = db.scalar(select(func.max(SaleLine.sale_date)))
    if last is None:
        return None
    compare_from = last - timedelta(days=window_days - 1)
    base_to = compare_from - timedelta(days=1)
    base_from = base_to - timedelta(days=window_days - 1)
    return base_from, base_to, compare_from, last


def compare_periods(
    db: Session,
    base_from: date,
    base_to: date,
    compare_from: date,
    compare_to: date,
) -> list[dict[str, Any]]:
    """Сравнение двух периодов по торговым точкам.

    Точка, у которой в одном из периодов нет документов, не отбрасывается: её показатели
    за отсутствующий период считаются нулевыми, а признак ``has_base``/``has_compare``
    показывает, что сравнение неполное. Молча пропускать такую точку нельзя — именно
    открытие или закрытие точки чаще всего и требует внимания.
    """
    base = {r["store_id"]: r for r in kpi_mart(db, base_from, base_to)}
    compare = {r["store_id"]: r for r in kpi_mart(db, compare_from, compare_to)}

    rows: list[dict[str, Any]] = []
    for store_id in sorted(base.keys() | compare.keys()):
        base_row = base.get(store_id)
        compare_row = compare.get(store_id)
        reference = base_row if base_row is not None else compare_row
        if reference is None:  # pragma: no cover - ключ взят из объединения словарей
            continue

        metrics: dict[str, Any] = {}
        for name in COMPARED_METRICS:
            current = float(compare_row[name]) if compare_row else 0.0
            previous = float(base_row[name]) if base_row else 0.0
            metrics[name] = {
                "base": previous,
                "compare": current,
                **_delta(current, previous),
            }

        base_margin = float(base_row["operating_margin_pct"]) if base_row else 0.0
        compare_margin = float(compare_row["operating_margin_pct"]) if compare_row else 0.0
        rows.append(
            {
                "store_id": store_id,
                "store_code": reference["store_code"],
                "store_name": reference["store_name"],
                "has_base": base_row is not None,
                "has_compare": compare_row is not None,
                "base_period": {"from": base_from.isoformat(), "to": base_to.isoformat()},
                "compare_period": {
                    "from": compare_from.isoformat(),
                    "to": compare_to.isoformat(),
                },
                "metrics": metrics,
                "operating_margin_pct": {
                    "base": base_margin,
                    "compare": compare_margin,
                    "absolute_pp": float(q2(Decimal(str(compare_margin - base_margin)))),
                },
                "margin_status": margin_status(compare_margin),
            }
        )
    return rows
