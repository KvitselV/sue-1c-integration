"""Контракт HTTP-API: коды ответов, формат ошибок, пагинация, безопасность."""

from __future__ import annotations

import json

import pytest

from sue import __version__
from tests import factories as f


def test_health_endpoints(client) -> None:
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/health/live").json()["status"] == "ok"
    ready = client.get("/api/health/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"]["database"] == "ok"


def test_version_reports_app_and_contract(client) -> None:
    body = client.get("/api/version").json()
    assert body["version"] == __version__
    assert body["contract_version"] == "2.0"
    assert body["app_env"] == "test"


def test_openapi_schema_is_generated(client) -> None:
    schema = client.get("/openapi.json").json()
    assert "/api/profitability" in schema["paths"]
    assert "ProfitabilityOut" in schema["components"]["schemas"]


def test_stores_are_paginated(client) -> None:
    body = client.get("/api/stores?limit=1&offset=0").json()
    assert len(body["items"]) == 1
    assert body["total"] >= 1
    assert body["limit"] == 1


def test_invalid_paging_is_rejected(client) -> None:
    response = client.get("/api/stores?limit=0")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_page_size_above_configured_maximum_is_rejected(client) -> None:
    response = client.get("/api/stores?limit=5000")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_paging"


def test_profitability_declares_provenance_for_every_value(client) -> None:
    rows = client.get("/api/profitability").json()
    assert rows
    for row in rows:
        for key in ("revenue", "cost", "gross_profit", "overhead", "operating_profit"):
            assert row[key]["source"] in {
                "accounting",
                "modeled",
                "derived",
                "accounting+modeled",
            }
            assert row[key]["note"]


def test_profitability_period_filter_narrows_revenue(client) -> None:
    everything = client.get("/api/profitability").json()
    narrow = client.get("/api/profitability?date_from=2026-01-01&date_to=2026-01-31").json()
    assert narrow
    assert sum(r["revenue"]["value"] for r in narrow) < sum(
        r["revenue"]["value"] for r in everything
    )


def test_reversed_period_is_rejected(client) -> None:
    response = client.get("/api/profitability?date_from=2026-05-01&date_to=2026-01-01")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_period"


def test_unknown_store_returns_structured_error(client) -> None:
    response = client.get("/api/profitability/9999")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "no_data"
    assert body["error"]["details"]["store_id"] == 9999


def test_store_pdf_report_is_downloadable(client) -> None:
    response = client.get("/api/profitability/1/report.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")
    assert "sue-" in response.headers["content-disposition"]
    assert response.headers["content-disposition"].endswith('.pdf"')


def test_network_pdf_report_is_downloadable(client) -> None:
    response = client.get("/api/profitability/report.pdf")
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_store_pdf_unknown_store_is_404(client) -> None:
    response = client.get("/api/profitability/9999/report.pdf")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_network_pdf_without_data_is_404(empty_client) -> None:
    response = empty_client.get("/api/profitability/report.pdf")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "no_data"


def test_csv_export_is_utf8_with_bom(client) -> None:
    response = client.get("/api/profitability.csv")
    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert "attachment" in response.headers["content-disposition"]
    assert "Выручка" in response.content.decode("utf-8-sig").splitlines()[0]


def test_categories_endpoint(client) -> None:
    rows = client.get("/api/profitability/1/categories").json()
    assert rows
    assert {"category", "revenue", "gross_margin_pct"} <= set(rows[0])


def test_etl_runs_are_paginated_and_filterable(client) -> None:
    body = client.get("/api/etl/runs?limit=2").json()
    assert len(body["items"]) <= 2
    filtered = client.get("/api/etl/runs?status=success").json()
    assert all(item["status"] == "success" for item in filtered["items"])


def test_errors_of_unknown_run_is_404(client) -> None:
    assert client.get("/api/etl/runs/999999/errors").status_code == 404


def test_import_by_scenario_name(client) -> None:
    response = client.post("/api/etl/import", json={"path": "short"})
    assert response.status_code == 201
    assert response.json()["status"] == "success"


def test_dry_run_import_does_not_change_totals(client) -> None:
    before = client.get("/api/reconciliation?path=main").json()["db_totals"]
    response = client.post("/api/etl/import", json={"path": "main", "dry_run": True})
    assert response.json()["dry_run"] is True
    assert client.get("/api/reconciliation?path=main").json()["db_totals"] == before


@pytest.mark.parametrize(
    "path",
    ["../../../etc/passwd", "..", "/etc/passwd", "C:/Windows/win.ini", "main/../../secret"],
)
def test_path_traversal_is_blocked(client, path: str) -> None:
    response = client.post("/api/etl/import", json={"path": path})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsafe_path"


def test_unknown_path_is_404(client) -> None:
    response = client.post("/api/etl/import", json={"path": "absent"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "path_not_found"


def test_unexpected_field_in_body_is_rejected(client) -> None:
    response = client.post("/api/etl/import", json={"path": "main", "truncate": True})
    assert response.status_code == 422


def test_upload_accepts_valid_batch(client) -> None:
    payload = json.dumps(f.batch(exchange_id="EX-UPLOAD")).encode("utf-8")
    response = client.post(
        "/api/etl/upload", files={"file": ("batch.json", payload, "application/json")}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "success"
    assert body["exchange_id"] == "EX-UPLOAD"


def test_upload_rejects_empty_file(client) -> None:
    response = client.post("/api/etl/upload", files={"file": ("e.json", b"", "application/json")})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "empty_file"


def test_upload_of_broken_json_is_audited_as_failed(client) -> None:
    response = client.post(
        "/api/etl/upload", files={"file": ("b.json", b"{oops", "application/json")}
    )
    assert response.status_code == 201
    assert response.json()["status"] == "failed"


def test_upload_larger_than_limit_is_rejected(client) -> None:
    """Предел размера должен срабатывать до разбора JSON, иначе он бесполезен."""
    from sue.config import Settings, get_settings
    from sue.main import app

    small_limit = get_settings().model_copy(update={"max_upload_bytes": 1024})
    app.dependency_overrides[get_settings] = lambda: small_limit
    try:
        payload = json.dumps(f.batch(documents=f.weekly_documents(200))).encode("utf-8")
        assert len(payload) > small_limit.max_upload_bytes
        response = client.post(
            "/api/etl/upload", files={"file": ("big.json", payload, "application/json")}
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 413
    body = response.json()
    assert body["error"]["code"] == "file_too_large"
    assert body["error"]["details"]["limit_bytes"] == 1024
    assert isinstance(small_limit, Settings)


def test_local_path_import_can_be_disabled(client) -> None:
    """В production приём данных ограничивается загрузкой файла."""
    from sue.config import get_settings
    from sue.main import app

    locked = get_settings().model_copy(update={"allow_local_path_import": False})
    app.dependency_overrides[get_settings] = lambda: locked
    try:
        response = client.post("/api/etl/import", json={"path": "main"})
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "local_import_disabled"


def test_readiness_reports_503_when_database_is_unavailable(client) -> None:
    from sqlalchemy.exc import OperationalError

    from sue.db import get_db
    from sue.main import app

    class BrokenSession:
        def execute(self, *_args: object, **_kwargs: object) -> None:
            raise OperationalError("SELECT 1", {}, Exception("соединение потеряно"))

    app.dependency_overrides[get_db] = lambda: BrokenSession()
    try:
        response = client.get("/api/health/ready")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"] == "unavailable"
    # Живость не зависит от БД: процесс работает, обслуживать запросы пока не может.
    assert client.get("/api/health/live").status_code == 200


def test_reconciliation_reports_zero_diff(client) -> None:
    body = client.get("/api/reconciliation?path=main").json()
    assert body["matched"] is True
    assert set(body["diff_minor_units"].values()) == {0}


def test_reconciliation_does_not_disclose_server_paths(client) -> None:
    """Ответ должен ссылаться на имя внутри каталога обмена, а не на путь сервера."""
    body = client.get("/api/reconciliation?path=main").json()
    assert body["source"] == "main"


def test_empty_database_returns_empty_lists(empty_client) -> None:
    assert empty_client.get("/api/profitability").json() == []
    assert empty_client.get("/api/stores").json()["total"] == 0


def test_request_id_header_is_returned(client) -> None:
    response = client.get("/api/health", headers={"x-request-id": "abc123"})
    assert response.headers["x-request-id"] == "abc123"


def test_security_headers_are_set(client) -> None:
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "same-origin"
