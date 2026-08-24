"""Страницы интерфейса: рендеринг, отсутствие внешних загрузок, обработка пустых данных."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from sue.config import Settings, get_settings
from sue.main import app

PAGES = [
    "/",
    "/ui/profitability",
    "/ui/profitability/1",
    "/ui/compare",
    "/ui/reports",
    "/ui/exchange",
    "/ui/etl",
]


@pytest.mark.parametrize("url", PAGES)
def test_page_renders(client, url: str) -> None:
    response = client.get(url)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "СУЭ" in response.text


@pytest.mark.parametrize("url", PAGES)
def test_page_has_no_external_resources(client, url: str) -> None:
    """Интерфейс должен работать без интернета: никаких CDN и внешних шрифтов."""
    html = client.get(url).text
    external = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    assert external == []


@pytest.mark.parametrize("url", PAGES)
def test_page_reports_no_undefined_template_values(client, url: str) -> None:
    html = client.get(url).text
    assert "Undefined" not in html
    assert "None" not in re.findall(r">\s*(None)\s*<", html)


def test_missing_store_shows_error_page(client) -> None:
    response = client.get("/ui/profitability/9999")
    assert response.status_code == 404
    assert "404" in response.text
    assert "На главную" in response.text


def test_reversed_period_is_rejected_on_pages(client) -> None:
    response = client.get("/ui/profitability?date_from=2026-06-01&date_to=2026-01-01")
    assert response.status_code == 422
    assert "Начало периода позже его конца" in response.text


def test_compare_rejects_partial_dates(client) -> None:
    response = client.get("/ui/compare?base_from=2026-01-01")
    assert response.status_code == 422
    assert "все четыре даты" in response.text


def test_store_card_distinguishes_missing_store_and_empty_period(client) -> None:
    missing = client.get("/ui/profitability/9999")
    assert missing.status_code == 404
    assert "не найдена" in missing.text

    empty_period = client.get("/ui/profitability/1?date_from=2099-01-01&date_to=2099-01-31")
    assert empty_period.status_code == 404
    assert "Нет данных" in empty_period.text


def test_etl_empty_database_does_not_claim_success(empty_client) -> None:
    html = empty_client.get("/ui/etl").text
    assert "Загрузок ещё не было" in html
    assert "пакет принят полностью" not in html


def test_store_page_has_period_filter(client) -> None:
    html = client.get("/ui/profitability/1").text
    assert 'name="date_from"' in html
    assert 'action="/ui/profitability/1"' in html


def test_period_inputs_use_styled_calendar(client) -> None:
    profit = client.get("/ui/profitability").text
    compare = client.get("/ui/compare").text
    assert profit.count('class="sue-date"') == 2
    assert compare.count('class="sue-date"') == 4
    css = client.get("/static/styles.css").text
    js = client.get("/static/app.js").text
    assert ".sue-cal-pop" in css
    assert "initDatePickers" in js


def test_period_filter_is_applied_to_page(client) -> None:
    response = client.get("/ui/profitability?date_from=2026-01-01&date_to=2026-01-31")
    assert response.status_code == 200
    assert 'value="2026-01-01"' in response.text


def test_pages_work_on_empty_database(empty_client) -> None:
    for url in ["/", "/ui/profitability", "/ui/reports", "/ui/exchange", "/ui/etl"]:
        response = empty_client.get(url)
        assert response.status_code == 200, url


def test_compare_page_shows_charts_like_profitability(client) -> None:
    html = client.get("/ui/compare").text
    assert 'id="cRevenue"' in html
    assert 'id="cMargins"' in html
    assert "compare-charts" in html


def test_compare_page_fills_periods_without_parameters(client) -> None:
    """Страница должна показывать осмысленное сравнение сразу, без ручного ввода четырёх дат."""
    html = client.get("/ui/compare").text
    assert 'name="base_from"' in html
    assert 'value=""' not in html.split('name="base_from"')[1].split(">")[0]
    assert "п. п." in html


def test_compare_page_keeps_given_periods(client) -> None:
    html = client.get(
        "/ui/compare?base_from=2026-01-01&base_to=2026-03-31"
        "&compare_from=2026-04-01&compare_to=2026-06-30"
    ).text
    assert 'value="2026-01-01"' in html
    assert 'value="2026-06-30"' in html


def test_compare_page_without_data_explains_what_to_do(empty_client) -> None:
    response = empty_client.get("/ui/compare")
    assert response.status_code == 404
    assert "загрузку данных" in response.text


def test_margin_traffic_light_is_shown_on_profitability_page(client) -> None:
    html = client.get("/ui/profitability").text
    assert any(label in html for label in ["норма", "внимание", "ниже нормы"])


def test_navigation_groups_analysis_and_data(client) -> None:
    html = client.get("/").text
    assert 'class="side"' in html
    assert 'class="nav-group"' in html
    assert 'class="nav-label"' in html
    assert 'class="top-link"' in html
    assert 'href="/docs"' in html
    assert 'href="/ui/reports"' in html
    assert 'href="/ui/exchange"' not in html
    assert "Документация API" not in html


def test_exchange_page_shows_export_as_tables(client) -> None:
    html = client.get("/ui/exchange").text
    assert "Выгрузка 1С" in html
    assert "не живая база 1С" in html
    assert 'class="xls-page"' in html
    assert 'class="side"' not in html
    assert ".xlsx" in html
    assert "Номенклатура" in html
    assert "Документы" in html
    assert "Строки" in html
    assert html.count("<table") >= 4


def test_reports_page_generates_network_and_store_pdf(client) -> None:
    html = client.get("/ui/reports").text
    assert "Отчёты" in html
    assert "Скачать PDF" in html
    assert "Скачать свод" not in html
    assert "/api/profitability/report.pdf" in html
    assert "/api/profitability/1/report.pdf" in html
    assert "report.pdf" not in client.get("/ui/profitability").text
    assert "report.pdf" not in client.get("/ui/profitability/1").text


def test_pages_put_explanations_on_hint_marks(client) -> None:
    """Длинные абзацы не занимают экран: пояснение открывается у знака вопроса."""
    html = client.get("/ui/profitability").text
    assert 'class="q"' in html
    assert "data-tip=" in html
    assert "Для передачи во внешнюю отчётность" not in html


def test_etl_page_offers_emulator_connection(client) -> None:
    html = client.get("/ui/etl").text
    assert "Эмулятор выгрузки 1С" in html
    assert 'id="pull-form"' in html
    assert "/emulator/1c/hs/exchange" in html


def test_upload_forms_are_shown_while_barrier_is_off(client) -> None:
    html = client.get("/ui/etl").text
    assert 'id="upload-form"' in html
    assert 'id="import-form"' not in html
    assert "Готовый сценарий" not in html


def test_etl_file_input_uses_styled_picker(client) -> None:
    html = client.get("/ui/etl").text
    assert 'class="sue-file"' in html
    css = client.get("/static/styles.css").text
    js = client.get("/static/app.js").text
    assert ".sue-file-trigger" in css
    assert "initFilePickers" in js


def test_upload_forms_are_hidden_when_key_is_configured(loaded_db) -> None:
    """Страница не должна предлагать загрузку, которую сама выполнить не может.

    Ключ в разметку не передаётся: любой открывший страницу получил бы его, и барьер
    потерял бы смысл. Поэтому формы скрываются, а способ загрузки указывается словами.
    """
    app.dependency_overrides[get_settings] = lambda: Settings(api_key="ui-key")  # type: ignore[call-arg]
    try:
        with TestClient(app) as secured:
            html = secured.get("/ui/etl").text
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert 'id="upload-form"' not in html
    assert 'id="import-form"' not in html
    assert 'id="pull-form"' not in html
    assert "X-API-Key" in html
    assert "ui-key" not in html


def test_layout_contains_charts_and_tables(client) -> None:
    """Графики и таблицы не должны раздувать страницу шире окна."""
    css = client.get("/static/styles.css").text
    assert "overflow-x: clip" in css
    assert "table.wide" in css
    assert "min-width: 720px" not in css
    assert ".side:hover" in css
    profit = client.get("/ui/profitability").text
    assert 'class="wide"' in profit


def test_static_assets_are_served(client) -> None:
    for path in [
        "/static/styles.css",
        "/static/app.js",
        "/static/vendor/chart.umd.min.js",
        "/static/favicon.png",
        "/static/sue-logo.png",
        "/static/sue-banner.jpg",
    ]:
        assert client.get(path).status_code == 200, path


def test_provenance_labels_are_in_russian(client) -> None:
    html = client.get("/ui/profitability").text
    assert "учёт" in html
    assert "модель" in html
    assert "расчёт" in html
