"""Эмулятор выгрузки и приём пакетов через POST /api/etl/pull."""

from __future__ import annotations


def test_emulator_ping_declares_it_is_not_live_1c(client) -> None:
    body = client.get("/emulator/1c/hs/exchange/ping").json()
    assert body["status"] == "ok"
    assert body["kind"] == "emulator"
    assert body["live_1c"] is False
    assert body["contract_version"] == "2.0"


def test_emulator_lists_and_returns_a_batch(client) -> None:
    listing = client.get("/emulator/1c/hs/exchange/batches?scenario=short").json()
    assert listing["count"] >= 1
    exchange_id = listing["items"][0]["exchange_id"]
    packet = client.get(f"/emulator/1c/hs/exchange/batches/{exchange_id}?scenario=short").json()
    assert packet["meta"]["exchangeId"] == exchange_id
    assert packet["meta"]["contractVersion"] == "2.0"


def test_emulator_unknown_scenario_is_404(client) -> None:
    response = client.get("/emulator/1c/hs/exchange/batches?scenario=does-not-exist")
    assert response.status_code == 404


def test_pull_from_builtin_emulator_loads_one_packet(empty_client) -> None:
    listing = empty_client.get("/emulator/1c/hs/exchange/batches?scenario=short").json()
    exchange_id = listing["items"][0]["exchange_id"]
    response = empty_client.post(
        "/api/etl/pull",
        json={"scenario": "short", "exchange_id": exchange_id},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "success"
    assert body["documents_accepted"] >= 1
    assert empty_client.get("/api/stores").json()["total"] >= 1


def test_pull_rejects_foreign_host(client) -> None:
    response = client.post(
        "/api/etl/pull",
        json={"url": "http://example.com/hs/exchange", "scenario": "short"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsafe_url"
