"""Агрегаты для страниц интерфейса.

Слой существует, чтобы шаблоны не обращались к БД и не считали показатели сами:
любая цифра на экране приходит из доменных функций, теми же путями, что и API.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sue.db.models import EtlRun, SaleLine
from sue.domain.profitability import (
    category_breakdown,
    compute_store_profitability,
    list_store_profitability,
)
from sue.domain.reconciliation import reconciliation_totals
from sue.i18n import STATUS_RU
from sue.money import from_kopecks

SENSITIVITY_DELTAS = (Decimal("-0.03"), Decimal("0"), Decimal("0.03"))


def _avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def weekly_revenue_series(db: Session, store_id: int) -> list[tuple[date, float]]:
    """Недельный ряд выручки в рублях. Недели без продаж заполняются нулём."""
    rows = db.execute(
        select(SaleLine.sale_date, func.sum(SaleLine.revenue_kopecks))
        .where(SaleLine.store_id == store_id)
        .group_by(SaleLine.sale_date)
        .order_by(SaleLine.sale_date)
    ).all()
    daily = [(row[0], int(row[1] or 0)) for row in rows]
    if not daily:
        return []

    buckets: dict[date, int] = {}
    for sale_date, kopecks in daily:
        key = _week_start(sale_date)
        buckets[key] = buckets.get(key, 0) + kopecks

    first, last = min(buckets), max(buckets)
    series: list[tuple[date, float]] = []
    cursor = first
    while cursor <= last:
        series.append((cursor, float(from_kopecks(buckets.get(cursor, 0)))))
        cursor += timedelta(days=7)
    return series


def dashboard_payload(
    db: Session,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    profit = list_store_profitability(db, date_from, date_to)
    recon = reconciliation_totals(db)
    runs = db.scalars(select(EtlRun).order_by(EtlRun.id.desc()).limit(12)).all()

    profit_sorted = sorted(profit, key=lambda r: r["revenue"]["value"], reverse=True)

    etl_stats = {"success": 0, "partial": 0, "failed": 0, "started": 0}
    for run in runs:
        etl_stats[run.status] = etl_stats.get(run.status, 0) + 1

    return {
        "recon": recon,
        "runs": runs,
        "profit": profit_sorted,
        "etl_stats": etl_stats,
        "period": {
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
        },
        "kpis": {
            "stores": len(profit),
            "revenue": sum(r["revenue"]["value"] for r in profit),
            "returns": sum(r["returns"]["value"] for r in profit),
            "gross_profit": sum(r["gross_profit"]["value"] for r in profit),
            "operating_profit": sum(r["operating_profit"]["value"] for r in profit),
            "avg_gross_margin": _avg([r["gross_margin_pct"]["value"] for r in profit]),
            "avg_op_margin": _avg([r["operating_margin_pct"]["value"] for r in profit]),
            "etl_success": etl_stats.get("success", 0),
            "etl_failed": etl_stats.get("failed", 0),
        },
        "charts": {
            "revenue_by_store": {
                "labels": [r["store_code"] for r in profit_sorted],
                "values": [r["revenue"]["value"] for r in profit_sorted],
            },
            "margins_by_store": {
                "labels": [r["store_code"] for r in profit_sorted],
                "gross": [r["gross_margin_pct"]["value"] for r in profit_sorted],
                "operating": [r["operating_margin_pct"]["value"] for r in profit_sorted],
            },
            "cost_mix": {
                "labels": [r["store_code"] for r in profit_sorted],
                "accounting": [r["cost_accounting_share_pct"] for r in profit_sorted],
                "modeled": [r["cost_modeled_share_pct"] for r in profit_sorted],
            },
            "profit_structure": {
                "labels": [r["store_code"] for r in profit_sorted],
                "cost": [r["cost"]["value"] for r in profit_sorted],
                "overhead": [r["overhead"]["value"] for r in profit_sorted],
                "operating": [r["operating_profit"]["value"] for r in profit_sorted],
            },
            "etl_pie": {
                "labels": [STATUS_RU.get(k, k) for k in etl_stats],
                "values": list(etl_stats.values()),
            },
        },
    }


def store_analytics(
    db: Session,
    store_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any] | None:
    item = compute_store_profitability(db, store_id, date_from, date_to)
    if item is None:
        return None

    series = weekly_revenue_series(db, store_id)
    categories = category_breakdown(db, store_id, date_from, date_to)

    sensitivity = []
    for delta in SENSITIVITY_DELTAS:
        scenario = compute_store_profitability(
            db, store_id, date_from, date_to, sensitivity_delta=delta
        )
        if scenario is None:
            continue
        sensitivity.append(
            {
                "delta": float(delta),
                "overhead_rate_pct": float(scenario.overhead_rate),
                "operating_margin_pct": float(scenario.operating_margin_pct.value),
                "operating_profit": float(scenario.operating_profit.value),
                "overhead": float(scenario.overhead.value),
            }
        )

    return {
        "item": item.to_dict(),
        "categories": categories,
        "sensitivity": sensitivity,
        "period": {
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
        },
        "charts": {
            "weekly_revenue": {
                "labels": [d.isoformat() for d, _ in series],
                "values": [round(v, 2) for _, v in series],
            },
            "waterfall": {
                "labels": [
                    "Выручка",
                    "Себестоимость",
                    "Вал. прибыль",
                    "Накладные",
                    "Опер. прибыль",
                ],
                "values": [
                    float(item.revenue.value),
                    -float(item.cost.value),
                    float(item.gross_profit.value),
                    -float(item.overhead.value),
                    float(item.operating_profit.value),
                ],
            },
            "cost_donut": {
                "labels": ["Себестоимость (учёт)", "Себестоимость (модель)"],
                "values": [
                    float(item.cost_accounting.value),
                    float(item.cost_modeled.value),
                ],
            },
            "categories": {
                "labels": [c["category"] for c in categories],
                "revenue": [c["revenue"] for c in categories],
                "gross_margin": [c["gross_margin_pct"] for c in categories],
            },
            "sensitivity": {
                "labels": [f"накладные {s['overhead_rate_pct']:.1f}%" for s in sensitivity],
                "op_margin": [s["operating_margin_pct"] for s in sensitivity],
                "op_profit": [s["operating_profit"] for s in sensitivity],
            },
        },
    }


def _ru_date_span(start: str, end: str) -> str:
    a = date.fromisoformat(start)
    b = date.fromisoformat(end)
    return f"{a.strftime('%d.%m')}–{b.strftime('%d.%m')}"


def compare_page_payload(rows: list[dict[str, Any]], periods: dict[str, str]) -> dict[str, Any]:
    """Сводка и серии графиков для страницы сравнения.

    Считает только по уже готовым строкам ``compare_periods``: шаблон не ходит в БД
    и не дублирует арифметику таблицы.
    """
    revenue_base = [float(r["metrics"]["revenue"]["base"]) for r in rows]
    revenue_compare = [float(r["metrics"]["revenue"]["compare"]) for r in rows]
    margin_base = [float(r["operating_margin_pct"]["base"]) for r in rows]
    margin_compare = [float(r["operating_margin_pct"]["compare"]) for r in rows]
    avg_op_base = _avg(margin_base)
    avg_op_compare = _avg(margin_compare)
    return {
        "kpis": {
            "stores": len(rows),
            "revenue_base": sum(revenue_base),
            "revenue_compare": sum(revenue_compare),
            "revenue_delta": sum(revenue_compare) - sum(revenue_base),
            "avg_op_base": avg_op_base,
            "avg_op_compare": avg_op_compare,
            "avg_op_pp": round(avg_op_compare - avg_op_base, 2),
        },
        "charts": {
            "labels": [r["store_code"] for r in rows],
            "base_label": _ru_date_span(periods["base_from"], periods["base_to"]),
            "compare_label": _ru_date_span(periods["compare_from"], periods["compare_to"]),
            "revenue_base": revenue_base,
            "revenue_compare": revenue_compare,
            "margin_base": margin_base,
            "margin_compare": margin_compare,
        },
    }
