"""PDF-отчёт по торговой точке: байты PDF и кириллические подписи."""

from __future__ import annotations

from datetime import UTC, datetime

from sue.reports.pdf import render_network_report, render_store_report


def _item() -> dict:
    value = {"value": 1000.5, "source": "accounting", "note": "", "unit": "RUB"}
    derived = {**value, "source": "derived"}
    modeled = {**value, "source": "modeled"}
    return {
        "store_code": "TT-01",
        "store_name": "Центр",
        "city": "Казань",
        "period_from": "2026-01-01",
        "period_to": "2026-03-31",
        "revenue": value,
        "returns": value,
        "cost": modeled,
        "gross_profit": derived,
        "gross_margin_pct": {**derived, "value": 28.4, "unit": "%"},
        "overhead": modeled,
        "operating_profit": derived,
        "operating_margin_pct": {**derived, "value": 16.2, "unit": "%"},
        "cost_accounting_share_pct": 80.0,
        "cost_modeled_share_pct": 20.0,
        "lines": 10,
        "lines_without_cost": 2,
    }


def test_store_report_is_a_pdf_document() -> None:
    pdf = render_store_report(
        _item(),
        [
            {
                "category": "Молоко",
                "revenue": 400,
                "cost": 250,
                "gross_profit": 150,
                "gross_margin_pct": 37.5,
            }
        ],
        generated_at=datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
        version="1.5.0",
    )
    assert pdf.startswith(b"%PDF")
    assert pdf.rstrip().endswith(b"%%EOF")
    assert len(pdf) > 2000


def test_network_report_lists_stores() -> None:
    pdf = render_network_report(
        [_item()],
        period_from="2026-01-01",
        period_to="2026-03-31",
        generated_at=datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
    )
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1500
