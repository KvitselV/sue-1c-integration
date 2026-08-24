"""Витрина и сравнение периодов через HTTP-API."""

from __future__ import annotations

import csv
import io

COMPARE_QUERY = (
    "base_from=2026-01-01&base_to=2026-03-31&compare_from=2026-04-01&compare_to=2026-06-30"
)


def test_kpi_mart_returns_flat_rows(client) -> None:
    response = client.get("/api/mart/kpi")
    assert response.status_code == 200
    rows = response.json()
    assert rows
    for row in rows:
        assert all(not isinstance(value, dict) for value in row.values())
        assert row["margin_status"] in {"ok", "warn", "bad"}
        assert row["cost_source"] in {"accounting", "modeled", "accounting+modeled"}


def test_kpi_mart_csv_opens_in_excel(client) -> None:
    """Разделитель «;» и BOM: без них Excel показывает файл одной колонкой."""
    response = client.get("/api/mart/kpi.csv")
    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")

    text = response.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    assert rows[0][0] == "Код ТТ"
    assert "Оценка рентабельности" in rows[0]
    assert len(rows) > 1
    assert len(rows[1]) == len(rows[0])


def test_kpi_mart_is_filtered_by_period(client) -> None:
    full = client.get("/api/mart/kpi").json()
    narrow = client.get("/api/mart/kpi?date_from=2026-01-01&date_to=2026-01-31").json()
    assert narrow
    assert sum(r["revenue"] for r in narrow) < sum(r["revenue"] for r in full)


def test_compare_is_not_mistaken_for_store_identifier(client) -> None:
    """Маршрут сравнения объявлен до /profitability/{store_id} и не считается номером точки."""
    response = client.get(f"/api/profitability/compare?{COMPARE_QUERY}")
    assert response.status_code == 200
    rows = response.json()
    assert rows
    assert rows[0]["base_period"]["from"] == "2026-01-01"
    assert set(rows[0]["metrics"]) == {"revenue", "cost", "gross_profit", "operating_profit"}


def test_compare_rejects_inverted_period(client) -> None:
    response = client.get(
        "/api/profitability/compare"
        "?base_from=2026-03-31&base_to=2026-01-01"
        "&compare_from=2026-04-01&compare_to=2026-06-30"
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_period"
    assert error["details"]["period"] == "base"


def test_compare_requires_all_four_dates(client) -> None:
    response = client.get("/api/profitability/compare?base_from=2026-01-01")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_mart_is_described_in_openapi(client) -> None:
    schema = client.get("/openapi.json").json()
    assert "/api/mart/kpi" in schema["paths"]
    assert "/api/profitability/compare" in schema["paths"]
