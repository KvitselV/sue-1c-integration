"""Контроль доступа: ключ в заголовке, открытые пробы, отказ единым форматом ошибки."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from sue.config import Settings, get_settings
from sue.main import app
from tests import factories as f

KEY = "test-key-3f9a"


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


@pytest.fixture
def secured_client(loaded_db) -> Iterator[TestClient]:
    """Клиент приложения с включённым барьером на изменяющих операциях."""
    app.dependency_overrides[get_settings] = lambda: _settings(api_key=KEY)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def fully_secured_client(loaded_db) -> Iterator[TestClient]:
    app.dependency_overrides[get_settings] = lambda: _settings(api_key=KEY, protect_read=True)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(get_settings, None)


def _upload(client: TestClient, headers: dict[str, str] | None = None):
    payload = json.dumps(f.batch()).encode("utf-8")
    return client.post(
        "/api/etl/upload",
        files={"file": ("batch.json", payload, "application/json")},
        headers=headers or {},
    )


def test_upload_without_key_is_rejected(secured_client) -> None:
    response = _upload(secured_client)
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "unauthorized"
    assert "X-API-Key" in error["message"]


def test_upload_with_wrong_key_is_rejected(secured_client) -> None:
    response = _upload(secured_client, {"X-API-Key": "wrong-key"})
    assert response.status_code == 401


def test_key_of_other_length_is_rejected_without_error(secured_client) -> None:
    """Сравнение ключей не должно падать на неожидаемом значении — только отказывать."""
    for wrong in ["", KEY[:-1], KEY + "x"]:
        assert _upload(secured_client, {"X-API-Key": wrong}).status_code == 401


def test_upload_with_key_is_accepted(secured_client) -> None:
    response = _upload(secured_client, {"X-API-Key": KEY})
    assert response.status_code == 201


def test_import_by_path_is_protected(secured_client) -> None:
    body = {"path": "main"}
    assert secured_client.post("/api/etl/import", json=body).status_code == 401
    assert (
        secured_client.post("/api/etl/import", json=body, headers={"X-API-Key": KEY}).status_code
        == 201
    )


def test_pull_from_emulator_is_protected(secured_client) -> None:
    body = {"scenario": "short"}
    assert secured_client.post("/api/etl/pull", json=body).status_code == 401
    assert (
        secured_client.post("/api/etl/pull", json=body, headers={"X-API-Key": KEY}).status_code
        == 201
    )


def test_emulator_ping_stays_open_when_reads_are_protected(fully_secured_client) -> None:
    """Учебный сервис выгрузки — источник, а не данные СУЭ: проба не требует ключа."""
    response = fully_secured_client.get("/emulator/1c/hs/exchange/ping")
    assert response.status_code == 200
    assert response.json()["live_1c"] is False


def test_reading_stays_open_while_only_writing_is_protected(secured_client) -> None:
    """Барьер по умолчанию закрывает только запись: демонстрация показателей не требует ключа."""
    assert secured_client.get("/api/profitability").status_code == 200
    assert secured_client.get("/api/mart/kpi").status_code == 200


def test_protect_read_closes_data_endpoints(fully_secured_client) -> None:
    assert fully_secured_client.get("/api/profitability").status_code == 401
    assert fully_secured_client.get("/api/profitability/report.pdf").status_code == 401
    assert fully_secured_client.get("/api/mart/kpi").status_code == 401
    assert fully_secured_client.get("/api/mart/kpi", headers={"X-API-Key": KEY}).status_code == 200


@pytest.mark.parametrize("url", ["/api/health", "/api/health/live", "/api/health/ready"])
def test_probes_stay_open_even_with_protected_reads(fully_secured_client, url: str) -> None:
    """Пробы оркестратора не должны знать секретов, иначе развёртывание считает сервис мёртвым."""
    assert fully_secured_client.get(url).status_code == 200


def test_version_stays_open_for_deployment_check(fully_secured_client) -> None:
    assert fully_secured_client.get("/api/version").status_code == 200


def test_key_is_absent_by_default(client) -> None:
    """Без настройки ключа прототип запускается и демонстрируется одной командой."""
    assert get_settings().auth_enabled is False
    assert _upload(client).status_code == 201


def test_protected_reading_requires_key_to_be_configured() -> None:
    """Защита чтения без ключа означала бы недоступный сервис — это отказ на старте."""
    with pytest.raises(ValueError, match="SUE_PROTECT_READ"):
        _settings(protect_read=True, api_key="")
