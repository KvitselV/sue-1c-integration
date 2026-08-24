"""Расчёт рентабельности: значения проверяются против чисел, посчитанных вручную."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from sue.datagen.catalog import COST_RATIO_BY_CATEGORY
from sue.domain.profitability import (
    CategoryAggregate,
    build_profitability,
    compute_store_profitability,
    load_cost_ratios,
)
from sue.domain.provenance import ACCOUNTING, ACCOUNTING_MODELED, DERIVED, MODELED
from sue.etl.pipeline import EtlPipeline


class _Store:
    id = 1
    code = "TT-01"
    name = "ТТ Тестовая"
    city = "Казань"


PERIOD = (date(2026, 1, 1), date(2026, 1, 31))


def _build(aggregates: list[CategoryAggregate], rate: str = "0.10"):
    return build_profitability(
        _Store(),  # type: ignore[arg-type]
        aggregates,
        PERIOD,
        overhead_rate=Decimal(rate),
        cost_ratios={"Бакалея": Decimal("0.50")},
        default_cost_ratio=Decimal("0.65"),
    )


def test_all_accounting_cost_gives_accounting_provenance() -> None:
    item = _build(
        [
            CategoryAggregate(
                category="Бакалея",
                sales_kopecks=100_000,  # 1000.00
                returns_kopecks=0,
                quantity_milli=10_000,
                cost_accounting_kopecks=60_000,  # 600.00
                revenue_without_cost_kopecks=0,
                lines=5,
                lines_without_cost=0,
            )
        ]
    )
    assert item.revenue.value == Decimal("1000.00")
    assert item.cost.source == ACCOUNTING
    assert item.cost.value == Decimal("600.00")
    assert item.gross_profit.value == Decimal("400.00")
    assert item.gross_profit.source == DERIVED
    assert item.overhead.value == Decimal("100.00")  # 1000 × 10%
    assert item.overhead.source == MODELED
    assert item.operating_profit.value == Decimal("300.00")
    assert item.gross_margin_pct.value == Decimal("40.00")
    assert item.operating_margin_pct.value == Decimal("30.00")


def test_missing_cost_is_modeled_by_category_ratio() -> None:
    item = _build(
        [
            CategoryAggregate(
                category="Бакалея",
                sales_kopecks=100_000,
                returns_kopecks=0,
                quantity_milli=10_000,
                cost_accounting_kopecks=0,
                revenue_without_cost_kopecks=100_000,
                lines=4,
                lines_without_cost=4,
            )
        ]
    )
    assert item.cost.source == MODELED
    assert item.cost.value == Decimal("500.00")  # 1000 × 0.50
    assert item.cost_modeled_share_pct == Decimal("100.00")


def test_unknown_category_uses_default_ratio() -> None:
    item = _build(
        [
            CategoryAggregate(
                category="Неизвестная",
                sales_kopecks=100_000,
                returns_kopecks=0,
                quantity_milli=1_000,
                cost_accounting_kopecks=0,
                revenue_without_cost_kopecks=100_000,
                lines=1,
                lines_without_cost=1,
            )
        ]
    )
    assert item.cost.value == Decimal("650.00")


def test_mixed_cost_is_marked_as_mixed() -> None:
    item = _build(
        [
            CategoryAggregate(
                category="Бакалея",
                sales_kopecks=200_000,
                returns_kopecks=0,
                quantity_milli=20_000,
                cost_accounting_kopecks=60_000,
                revenue_without_cost_kopecks=100_000,
                lines=10,
                lines_without_cost=4,
            )
        ]
    )
    assert item.cost.source == ACCOUNTING_MODELED
    assert item.cost.value == Decimal("1100.00")  # 600 учёт + 500 модель
    assert item.cost_accounting_share_pct == Decimal("54.55")
    assert "4 строк смоделировано" in item.cost.note


def test_returns_reduce_revenue_but_are_shown_separately() -> None:
    item = _build(
        [
            CategoryAggregate(
                category="Бакалея",
                sales_kopecks=100_000,
                returns_kopecks=-20_000,
                quantity_milli=8_000,
                cost_accounting_kopecks=50_000,
                revenue_without_cost_kopecks=0,
                lines=6,
                lines_without_cost=0,
            )
        ]
    )
    assert item.gross_revenue.value == Decimal("1000.00")
    assert item.returns.value == Decimal("200.00")
    assert item.revenue.value == Decimal("800.00")
    assert item.overhead.value == Decimal("80.00")  # накладные считаются от нетто-выручки


def test_zero_revenue_does_not_divide_by_zero() -> None:
    item = _build(
        [
            CategoryAggregate(
                category="Бакалея",
                sales_kopecks=50_000,
                returns_kopecks=-50_000,
                quantity_milli=0,
                cost_accounting_kopecks=0,
                revenue_without_cost_kopecks=0,
                lines=2,
                lines_without_cost=0,
            )
        ]
    )
    assert item.revenue.value == Decimal("0.00")
    assert item.gross_margin_pct.value == Decimal("0.00")


def test_every_value_declares_provenance() -> None:
    item = _build([CategoryAggregate("Бакалея", 100_000, 0, 1_000, 60_000, 0, 1, 0)])
    for name in item._VALUE_FIELDS:
        value = getattr(item, name)
        assert value.source, name
        assert value.note, name


def test_sensitivity_shifts_overhead_only(db, fixtures_dir) -> None:
    EtlPipeline(db).run_file(fixtures_dir / "main")
    base = compute_store_profitability(db, 1)
    shifted = compute_store_profitability(db, 1, sensitivity_delta=0.03)
    assert base and shifted
    assert shifted.revenue.value == base.revenue.value
    assert shifted.cost.value == base.cost.value
    assert shifted.overhead.value > base.overhead.value
    assert shifted.operating_profit.value < base.operating_profit.value


def test_negative_overhead_rate_is_clamped_to_zero(db, fixtures_dir) -> None:
    EtlPipeline(db).run_file(fixtures_dir / "main")
    item = compute_store_profitability(db, 1, sensitivity_delta=-1.0)
    assert item is not None
    assert item.overhead.value == Decimal("0.00")


def test_missing_store_returns_none(db) -> None:
    assert compute_store_profitability(db, 12345) is None


def test_cost_ratios_are_loaded_from_fixtures(fixtures_dir) -> None:
    ratios = load_cost_ratios()
    assert ratios
    assert set(ratios) == set(COST_RATIO_BY_CATEGORY)


@pytest.mark.parametrize("scenario", ["nocost"])
def test_no_cost_scenario_is_fully_modeled(db, fixtures_dir, scenario: str) -> None:
    EtlPipeline(db).run_file(fixtures_dir / scenario)
    from sue.db.models import Store

    store = db.query(Store).first()
    item = compute_store_profitability(db, store.id)
    assert item is not None
    assert item.cost.source == MODELED
    assert item.cost_modeled_share_pct == Decimal("100.00")


def test_aggregate_matches_line_level_sum(loaded_db) -> None:
    """Агрегация в СУБД должна совпадать с суммой по строкам."""
    from sqlalchemy import func, select

    from sue.db.models import SaleLine

    item = compute_store_profitability(loaded_db, 1)
    raw = loaded_db.scalar(select(func.sum(SaleLine.revenue_kopecks)).where(SaleLine.store_id == 1))
    assert item is not None
    assert item.revenue.value == Decimal(raw) / 100


def test_dashboard_store_count_follows_filtered_period(loaded_db) -> None:
    from sue.domain.analytics import dashboard_payload

    full = dashboard_payload(loaded_db)
    assert full["kpis"]["stores"] == len(full["profit"])
    empty = dashboard_payload(loaded_db, date(2099, 1, 1), date(2099, 1, 31))
    assert empty["kpis"]["stores"] == 0
    assert empty["profit"] == []
