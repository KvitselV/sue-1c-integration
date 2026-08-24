"""Витрина показателей, светофор и сравнение периодов."""

from __future__ import annotations

from datetime import date

import pytest

from sue.adapter_1c import Batch
from sue.api.schemas import MartRowOut
from sue.config import get_settings
from sue.domain.analytics import compare_page_payload
from sue.domain.mart import (
    MART_COLUMNS,
    STATUS_BAD,
    STATUS_OK,
    STATUS_WARN,
    compare_periods,
    default_compare_periods,
    kpi_mart,
    margin_status,
)
from sue.etl.pipeline import EtlPipeline
from tests import factories as f

BASE_FROM = date(2026, 1, 5)
BASE_TO = date(2026, 1, 11)
COMPARE_FROM = date(2026, 1, 12)
COMPARE_TO = date(2026, 1, 18)


def _load(db, documents, stores=None) -> None:
    payload = f.batch(documents=documents, stores=stores)
    EtlPipeline(db).run_batch(Batch("t", payload))


# --- светофор -----------------------------------------------------------------


def test_margin_status_thresholds() -> None:
    settings = get_settings()
    between = (settings.margin_target_pct + settings.margin_warn_pct) / 2
    assert settings.margin_warn_pct < between < settings.margin_target_pct

    assert margin_status(settings.margin_target_pct + 5) == STATUS_OK
    assert margin_status(between) == STATUS_WARN
    assert margin_status(settings.margin_warn_pct - 1) == STATUS_BAD


def test_margin_status_on_threshold_is_the_better_grade() -> None:
    """Значение, равное порогу, относится к лучшей оценке.

    Иначе точка с рентабельностью ровно в целевом значении попадала бы
    в «внимание», хотя цель достигнута.
    """
    settings = get_settings()
    assert margin_status(settings.margin_target_pct) == STATUS_OK
    assert margin_status(settings.margin_warn_pct) == STATUS_WARN


def test_negative_margin_is_below_norm() -> None:
    assert margin_status(-12.5) == STATUS_BAD


# --- витрина ------------------------------------------------------------------


def test_mart_row_is_flat_and_matches_schema(db) -> None:
    """Во витрине не должно быть вложенных объектов: её читает внешняя отчётность."""
    _load(db, [f.document("d1", "2026-01-05", [f.line("l1", 1000.0, cost=600.0)])])

    rows = kpi_mart(db)
    assert len(rows) == 1
    row = rows[0]
    assert all(not isinstance(value, dict) for value in row.values())
    MartRowOut.model_validate(row)


def test_mart_keeps_provenance_of_cost(db) -> None:
    """Плоская форма не должна скрывать, что часть себестоимости смоделирована."""
    _load(
        db,
        [
            f.document("d1", "2026-01-05", [f.line("l1", 1000.0, cost=600.0)]),
            f.document("d2", "2026-01-06", [f.line("l2", 1000.0)]),
        ],
    )

    row = kpi_mart(db)[0]
    assert row["cost_source"] == "accounting+modeled"
    assert row["cost_modeled_share_pct"] > 0
    assert row["cost_accounting_share_pct"] > 0
    assert row["lines_without_cost"] == 1


def test_mart_columns_cover_every_row_field(db) -> None:
    """Выгрузка CSV не должна терять поля витрины при их добавлении."""
    _load(db, [f.document("d1", "2026-01-05", [f.line("l1", 500.0, cost=300.0)])])

    row = kpi_mart(db)[0]
    exported = {key for key, _ in MART_COLUMNS}
    missing = set(row) - exported - {"store_id"}
    assert missing == set()


def test_mart_respects_period_filter(db) -> None:
    _load(
        db,
        [
            f.document("d1", "2026-01-05", [f.line("l1", 1000.0, cost=600.0)]),
            f.document("d2", "2026-02-05", [f.line("l2", 4000.0, cost=2400.0)]),
        ],
    )

    row = kpi_mart(db, date(2026, 1, 1), date(2026, 1, 31))[0]
    assert row["revenue"] == pytest.approx(1000.0)
    # В строке витрины стоят границы запрошенного периода: получатель должен видеть,
    # за какой интервал посчитан показатель, даже если документов в его начале нет.
    assert (row["period_from"], row["period_to"]) == ("2026-01-01", "2026-01-31")


# --- сравнение периодов -------------------------------------------------------


def test_compare_reports_absolute_and_relative_change(db) -> None:
    _load(
        db,
        [
            f.document("d1", "2026-01-05", [f.line("l1", 1000.0, cost=600.0)]),
            f.document("d2", "2026-01-12", [f.line("l2", 1500.0, cost=900.0)]),
        ],
    )

    rows = compare_periods(db, BASE_FROM, BASE_TO, COMPARE_FROM, COMPARE_TO)
    assert len(rows) == 1
    revenue = rows[0]["metrics"]["revenue"]
    assert revenue["base"] == pytest.approx(1000.0)
    assert revenue["compare"] == pytest.approx(1500.0)
    assert revenue["absolute"] == pytest.approx(500.0)
    assert revenue["relative_pct"] == pytest.approx(50.0)

    cost = rows[0]["metrics"]["cost"]
    assert cost["absolute"] == pytest.approx(300.0)
    assert cost["relative_pct"] == pytest.approx(50.0)


def test_compare_marks_margin_change_in_percentage_points(db) -> None:
    """Разница рентабельностей — процентные пункты, а не проценты от процента."""
    _load(
        db,
        [
            f.document("d1", "2026-01-05", [f.line("l1", 1000.0, cost=600.0)]),
            f.document("d2", "2026-01-12", [f.line("l2", 1000.0, cost=500.0)]),
        ],
    )

    margin = compare_periods(db, BASE_FROM, BASE_TO, COMPARE_FROM, COMPARE_TO)[0][
        "operating_margin_pct"
    ]
    assert margin["absolute_pp"] == pytest.approx(margin["compare"] - margin["base"], abs=0.01)
    assert margin["absolute_pp"] > 0


def test_compare_keeps_store_absent_in_one_period(db) -> None:
    """Открытие точки — это как раз то, что нужно увидеть, а не отбросить."""
    _load(
        db,
        stores=[f.store("s1", "TT-01"), f.store("s2", "TT-02")],
        documents=[
            f.document("d1", "2026-01-05", [f.line("l1", 1000.0, cost=600.0)]),
            f.document("d2", "2026-01-12", [f.line("l2", 800.0, cost=500.0)], store_ref="s2"),
        ],
    )

    compared = compare_periods(db, BASE_FROM, BASE_TO, COMPARE_FROM, COMPARE_TO)
    rows = {r["store_code"]: r for r in compared}
    opened = rows["TT-02"]
    assert opened["has_base"] is False
    assert opened["has_compare"] is True
    assert opened["metrics"]["revenue"]["base"] == pytest.approx(0.0)
    assert opened["metrics"]["revenue"]["compare"] == pytest.approx(800.0)

    closed = rows["TT-01"]
    assert closed["has_compare"] is False
    assert closed["metrics"]["revenue"]["compare"] == pytest.approx(0.0)


def test_relative_change_from_zero_base_is_undefined(db) -> None:
    """При нулевой базе рост нельзя выразить в процентах — возвращается «не определено»."""
    _load(db, [f.document("d2", "2026-01-12", [f.line("l2", 800.0, cost=500.0)])])

    revenue = compare_periods(db, BASE_FROM, BASE_TO, COMPARE_FROM, COMPARE_TO)[0]["metrics"][
        "revenue"
    ]
    assert revenue["base"] == pytest.approx(0.0)
    assert revenue["relative_pct"] is None
    assert revenue["absolute"] == pytest.approx(800.0)


def test_default_periods_are_two_adjacent_windows_ending_at_last_document(db) -> None:
    """Окна отсчитываются от последней даты в данных, а не от текущей."""
    _load(db, f.weekly_documents(10, amount=1000.0, start="2026-01-05"))

    periods = default_compare_periods(db, window_days=28)
    assert periods is not None
    base_from, base_to, compare_from, compare_to = periods
    assert (base_to - base_from).days == 27
    assert (compare_to - compare_from).days == 27
    assert (compare_from - base_to).days == 1
    assert compare_to == date(2026, 3, 9)


def test_default_periods_are_absent_without_data(db) -> None:
    assert default_compare_periods(db) is None


def test_compare_page_payload_builds_grouped_chart_series(db) -> None:
    _load(db, f.weekly_documents(2, amount=1000.0, start="2026-01-05"))
    rows = compare_periods(db, BASE_FROM, BASE_TO, COMPARE_FROM, COMPARE_TO)
    payload = compare_page_payload(
        rows,
        {
            "base_from": BASE_FROM.isoformat(),
            "base_to": BASE_TO.isoformat(),
            "compare_from": COMPARE_FROM.isoformat(),
            "compare_to": COMPARE_TO.isoformat(),
        },
    )
    charts = payload["charts"]
    assert charts["labels"]
    assert charts["base_label"] == "05.01–11.01"
    assert charts["compare_label"] == "12.01–18.01"
    assert len(charts["revenue_base"]) == len(charts["labels"])
    assert payload["kpis"]["stores"] == len(rows)
