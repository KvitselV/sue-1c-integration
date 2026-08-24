"""Табличный просмотр пакета обмена читает файл, а не базу СУЭ."""

from __future__ import annotations

from pathlib import Path

from sue.domain.exchange_preview import LINE_LIMIT, exchange_preview


def test_exchange_preview_builds_tables_from_fixture_file(fixtures_dir: Path) -> None:
    data = exchange_preview(fixtures_dir)
    assert data["error"] is None
    assert data["scenario"]
    assert data["exchange_id"]
    assert data["stores"]
    assert data["products"]
    assert data["documents"]
    assert data["lines"]
    assert data["lines_total"] >= data["lines_shown"]
    assert data["lines_shown"] <= LINE_LIMIT
    first_store = data["stores"][0]
    assert first_store["code"]
    assert first_store["name"]


def test_exchange_preview_falls_back_when_scenario_unknown(fixtures_dir: Path) -> None:
    data = exchange_preview(fixtures_dir, scenario="нет-такого")
    assert data["error"] is None
    assert data["scenario"] != "нет-такого"
    names = {item["id"] for item in data["scenarios"]}
    assert data["scenario"] in names
