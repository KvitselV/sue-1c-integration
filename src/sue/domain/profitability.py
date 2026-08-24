"""Расчёт рентабельности торговых точек.

Методика (полностью соответствует реализации):

1. **Выручка** — сумма ``amount`` строк документов реализации за вычетом возвратов.
   Источник: учётные данные.
2. **Себестоимость** — ``costAmount`` там, где он выгружен из учёта; для остальных
   строк моделируется как ``выручка × доля себестоимости по категории``. Доля
   берётся из ``data/fixtures/modeled/cost_markup_by_category.json``.
3. **Валовая прибыль** = выручка − себестоимость (расчёт).
4. **Накладные расходы** = выручка × ставка аллокации. Ставка — моделируемый
   параметр, в учётных данных отсутствует.
5. **Операционная прибыль** = валовая прибыль − накладные (расчёт).

Все суммы считаются в копейках целыми числами и округляются один раз — при выдаче.
Агрегация выполняется на стороне СУБД: одним запросом на все точки, без выгрузки
строк документов в память.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache
from typing import Any

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from sue.config import get_settings
from sue.db.models import DOC_TYPE_RETURN, DOC_TYPE_SALE, Product, SaleLine, Store
from sue.domain.provenance import (
    ACCOUNTING,
    ACCOUNTING_MODELED,
    DERIVED,
    MODELED,
    ProvenanceValue,
)
from sue.money import apply_ratio, from_kopecks, from_milli, q2, share_pct


@dataclass(frozen=True)
class CategoryAggregate:
    """Свёртка строк документов по категории номенклатуры."""

    category: str
    sales_kopecks: int
    returns_kopecks: int
    quantity_milli: int
    cost_accounting_kopecks: int
    revenue_without_cost_kopecks: int
    lines: int
    lines_without_cost: int

    @property
    def net_revenue_kopecks(self) -> int:
        return self.sales_kopecks + self.returns_kopecks


@dataclass(frozen=True)
class StoreProfitability:
    store_id: int
    store_code: str
    store_name: str
    city: str | None
    period_from: date
    period_to: date
    overhead_rate: Decimal
    revenue: ProvenanceValue
    gross_revenue: ProvenanceValue
    returns: ProvenanceValue
    cost: ProvenanceValue
    cost_accounting: ProvenanceValue
    cost_modeled: ProvenanceValue
    gross_profit: ProvenanceValue
    gross_margin_pct: ProvenanceValue
    overhead: ProvenanceValue
    operating_profit: ProvenanceValue
    operating_margin_pct: ProvenanceValue
    quantity: ProvenanceValue
    cost_accounting_share_pct: Decimal
    cost_modeled_share_pct: Decimal
    lines: int
    lines_without_cost: int

    _VALUE_FIELDS = (
        "revenue",
        "gross_revenue",
        "returns",
        "cost",
        "cost_accounting",
        "cost_modeled",
        "gross_profit",
        "gross_margin_pct",
        "overhead",
        "operating_profit",
        "operating_margin_pct",
        "quantity",
    )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "store_id": self.store_id,
            "store_code": self.store_code,
            "store_name": self.store_name,
            "city": self.city,
            "period_from": self.period_from.isoformat(),
            "period_to": self.period_to.isoformat(),
            "overhead_rate": float(self.overhead_rate),
            "cost_accounting_share_pct": float(self.cost_accounting_share_pct),
            "cost_modeled_share_pct": float(self.cost_modeled_share_pct),
            "lines": self.lines,
            "lines_without_cost": self.lines_without_cost,
        }
        for name in self._VALUE_FIELDS:
            data[name] = getattr(self, name).to_dict()
        return data


# --- моделируемые параметры ---------------------------------------------------


@lru_cache(maxsize=4)
def _cost_ratios_cached(path_str: str, mtime: float) -> dict[str, Decimal]:
    from pathlib import Path

    with Path(path_str).open(encoding="utf-8") as f:
        raw = json.load(f)
    return {k: Decimal(str(v)) for k, v in raw.get("cost_ratio_by_category", {}).items()}


def load_cost_ratios() -> dict[str, Decimal]:
    path = get_settings().fixtures_dir / "modeled" / "cost_markup_by_category.json"
    if not path.exists():
        return {}
    return _cost_ratios_cached(str(path), path.stat().st_mtime)


def load_overhead_rate() -> Decimal:
    settings = get_settings()
    path = settings.fixtures_dir / "modeled" / "overhead_params.json"
    if path.exists():
        with path.open(encoding="utf-8") as f:
            raw = json.load(f)
        if "overhead_rate" in raw:
            return Decimal(str(raw["overhead_rate"]))
    return Decimal(str(settings.overhead_rate))


# --- агрегация ---------------------------------------------------------------


def _apply_period(stmt: Select[Any], date_from: date | None, date_to: date | None) -> Select[Any]:
    if date_from is not None:
        stmt = stmt.where(SaleLine.sale_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(SaleLine.sale_date <= date_to)
    return stmt


def aggregate_by_store_category(
    db: Session,
    store_ids: list[int] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[int, list[CategoryAggregate]]:
    """Свёртка строк документов по (торговая точка, категория) одним SQL-запросом."""
    is_sale = SaleLine.doc_type == DOC_TYPE_SALE
    is_return = SaleLine.doc_type == DOC_TYPE_RETURN
    no_cost = SaleLine.cost_accounting_kopecks.is_(None)

    stmt = (
        select(
            SaleLine.store_id,
            Product.category,
            func.coalesce(func.sum(case((is_sale, SaleLine.revenue_kopecks), else_=0)), 0),
            func.coalesce(func.sum(case((is_return, SaleLine.revenue_kopecks), else_=0)), 0),
            func.coalesce(func.sum(SaleLine.quantity_milli), 0),
            func.coalesce(func.sum(func.coalesce(SaleLine.cost_accounting_kopecks, 0)), 0),
            func.coalesce(func.sum(case((no_cost, SaleLine.revenue_kopecks), else_=0)), 0),
            func.count(SaleLine.id),
            func.coalesce(func.sum(case((no_cost, 1), else_=0)), 0),
        )
        .join(Product, Product.id == SaleLine.product_id)
        .group_by(SaleLine.store_id, Product.category)
    )
    if store_ids is not None:
        stmt = stmt.where(SaleLine.store_id.in_(store_ids))
    stmt = _apply_period(stmt, date_from, date_to)

    result: dict[int, list[CategoryAggregate]] = {}
    for row in db.execute(stmt).all():
        store_id = int(row[0])
        result.setdefault(store_id, []).append(
            CategoryAggregate(
                category=row[1],
                sales_kopecks=int(row[2]),
                returns_kopecks=int(row[3]),
                quantity_milli=int(row[4]),
                cost_accounting_kopecks=int(row[5]),
                revenue_without_cost_kopecks=int(row[6]),
                lines=int(row[7]),
                lines_without_cost=int(row[8]),
            )
        )
    return result


def store_periods(
    db: Session,
    store_ids: list[int] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[int, tuple[date, date]]:
    stmt = select(
        SaleLine.store_id, func.min(SaleLine.sale_date), func.max(SaleLine.sale_date)
    ).group_by(SaleLine.store_id)
    if store_ids is not None:
        stmt = stmt.where(SaleLine.store_id.in_(store_ids))
    stmt = _apply_period(stmt, date_from, date_to)
    return {int(sid): (dmin, dmax) for sid, dmin, dmax in db.execute(stmt).all()}


# --- расчёт показателей -------------------------------------------------------


def build_profitability(
    store: Store,
    aggregates: list[CategoryAggregate],
    period: tuple[date, date],
    *,
    overhead_rate: Decimal,
    cost_ratios: dict[str, Decimal],
    default_cost_ratio: Decimal,
) -> StoreProfitability:
    sales = sum(a.sales_kopecks for a in aggregates)
    returns = sum(a.returns_kopecks for a in aggregates)  # отрицательные величины
    quantity_milli = sum(a.quantity_milli for a in aggregates)
    cost_acc = sum(a.cost_accounting_kopecks for a in aggregates)
    lines = sum(a.lines for a in aggregates)
    lines_without_cost = sum(a.lines_without_cost for a in aggregates)

    cost_mod = 0
    for agg in aggregates:
        if agg.revenue_without_cost_kopecks:
            ratio = cost_ratios.get(agg.category, default_cost_ratio)
            cost_mod += apply_ratio(agg.revenue_without_cost_kopecks, ratio)

    revenue = sales + returns
    cost_total = cost_acc + cost_mod
    gross = revenue - cost_total
    overhead = apply_ratio(revenue, overhead_rate)
    operating = gross - overhead

    has_acc = lines - lines_without_cost > 0
    has_mod = lines_without_cost > 0
    if has_acc and has_mod:
        cost_source = ACCOUNTING_MODELED
        cost_note = (
            f"Себестоимость: {lines - lines_without_cost} строк из учёта (costAmount), "
            f"{lines_without_cost} строк смоделировано по доле категории"
        )
    elif has_acc:
        cost_source = ACCOUNTING
        cost_note = "Себестоимость из поля costAmount контракта обмена (учётные данные)"
    else:
        cost_source = MODELED
        cost_note = "Себестоимость смоделирована: выручка × доля себестоимости по категории"

    return StoreProfitability(
        store_id=store.id,
        store_code=store.code,
        store_name=store.name,
        city=store.city,
        period_from=period[0],
        period_to=period[1],
        overhead_rate=q2(overhead_rate * 100),
        revenue=ProvenanceValue(
            from_kopecks(revenue),
            ACCOUNTING,
            "Сумма реализации за вычетом возвратов",
        ),
        gross_revenue=ProvenanceValue(
            from_kopecks(sales), ACCOUNTING, "Сумма amount по документам реализации"
        ),
        returns=ProvenanceValue(
            from_kopecks(-returns), ACCOUNTING, "Сумма по документам возврата от покупателя"
        ),
        cost=ProvenanceValue(from_kopecks(cost_total), cost_source, cost_note),
        cost_accounting=ProvenanceValue(
            from_kopecks(cost_acc), ACCOUNTING, "Часть себестоимости, выгруженная из учёта"
        ),
        cost_modeled=ProvenanceValue(
            from_kopecks(cost_mod), MODELED, "Часть себестоимости, рассчитанная по доле категории"
        ),
        gross_profit=ProvenanceValue(from_kopecks(gross), DERIVED, "Выручка − себестоимость"),
        gross_margin_pct=ProvenanceValue(
            share_pct(gross, revenue), DERIVED, "Валовая прибыль / выручка × 100", unit="%"
        ),
        overhead=ProvenanceValue(
            from_kopecks(overhead),
            MODELED,
            f"Аллокация накладных расходов: выручка × {overhead_rate:.2%}",
        ),
        operating_profit=ProvenanceValue(
            from_kopecks(operating), DERIVED, "Валовая прибыль − накладные расходы"
        ),
        operating_margin_pct=ProvenanceValue(
            share_pct(operating, revenue),
            DERIVED,
            "Операционная прибыль / выручка × 100",
            unit="%",
        ),
        quantity=ProvenanceValue(
            from_milli(quantity_milli), ACCOUNTING, "Количество за вычетом возвратов", unit="шт"
        ),
        cost_accounting_share_pct=share_pct(cost_acc, cost_total),
        cost_modeled_share_pct=share_pct(cost_mod, cost_total),
        lines=lines,
        lines_without_cost=lines_without_cost,
    )


def compute_store_profitability(
    db: Session,
    store_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    overhead_rate: float | Decimal | None = None,
    sensitivity_delta: float | Decimal = 0.0,
) -> StoreProfitability | None:
    store = db.get(Store, store_id)
    if store is None:
        return None

    aggregates = aggregate_by_store_category(db, [store_id], date_from, date_to).get(store_id)
    if not aggregates:
        return None
    periods = store_periods(db, [store_id], date_from, date_to)
    dmin, dmax = periods[store_id]

    settings = get_settings()
    base_rate = Decimal(str(overhead_rate)) if overhead_rate is not None else load_overhead_rate()
    rate = max(Decimal("0"), base_rate + Decimal(str(sensitivity_delta)))

    return build_profitability(
        store,
        aggregates,
        (date_from or dmin, date_to or dmax),
        overhead_rate=rate,
        cost_ratios=load_cost_ratios(),
        default_cost_ratio=Decimal(str(settings.default_cost_ratio)),
    )


def list_store_profitability(
    db: Session,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    """Показатели по всем точкам. Три запроса независимо от числа точек."""
    stores = db.scalars(select(Store).order_by(Store.code)).all()
    if not stores:
        return []

    store_ids = [s.id for s in stores]
    aggregates = aggregate_by_store_category(db, store_ids, date_from, date_to)
    periods = store_periods(db, store_ids, date_from, date_to)

    settings = get_settings()
    rate = load_overhead_rate()
    ratios = load_cost_ratios()
    default_ratio = Decimal(str(settings.default_cost_ratio))

    result: list[dict[str, Any]] = []
    for store in stores:
        store_aggregates = aggregates.get(store.id)
        if not store_aggregates:
            continue
        dmin, dmax = periods[store.id]
        item = build_profitability(
            store,
            store_aggregates,
            (date_from or dmin, date_to or dmax),
            overhead_rate=rate,
            cost_ratios=ratios,
            default_cost_ratio=default_ratio,
        )
        result.append(item.to_dict())
    return result


def category_breakdown(
    db: Session,
    store_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    """Разрез выручки и себестоимости по категориям для одной точки."""
    aggregates = aggregate_by_store_category(db, [store_id], date_from, date_to).get(store_id, [])
    ratios = load_cost_ratios()
    default_ratio = Decimal(str(get_settings().default_cost_ratio))

    rows: list[dict[str, Any]] = []
    for agg in sorted(aggregates, key=lambda a: -a.net_revenue_kopecks):
        cost = agg.cost_accounting_kopecks + apply_ratio(
            agg.revenue_without_cost_kopecks, ratios.get(agg.category, default_ratio)
        )
        revenue = agg.net_revenue_kopecks
        rows.append(
            {
                "category": agg.category,
                "revenue": float(from_kopecks(revenue)),
                "cost": float(from_kopecks(cost)),
                "gross_profit": float(from_kopecks(revenue - cost)),
                "gross_margin_pct": float(share_pct(revenue - cost, revenue)),
                "lines": agg.lines,
            }
        )
    return rows
