"""Эмулятор выгрузки 1С и HTTP-источник."""

from __future__ import annotations

import json

import pytest

from sue.adapter_1c.http_source import HttpExchangeSource, UnsafeUrlError, resolve_export_url
from sue.emulator.catalog import EmulatorCatalog, EmulatorError
from tests import factories as f


def test_resolve_export_url_accepts_loopback() -> None:
    assert resolve_export_url("http://127.0.0.1:8001/") == "http://127.0.0.1:8001"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://example.com/hs/exchange",
        "https://user:pass@127.0.0.1/hs",
        "",
    ],
)
def test_resolve_export_url_rejects_unsafe_addresses(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        resolve_export_url(url, allowed_hosts=("127.0.0.1", "localhost"))


def test_catalog_lists_and_reads_scenario(fixtures_dir) -> None:
    catalog = EmulatorCatalog(fixtures_dir)
    assert "short" in catalog.scenarios()
    scenario = "short"
    items = catalog.list_batches(scenario)
    assert items
    batch = catalog.get_batch(scenario, items[0].exchange_id)
    assert batch.payload["meta"]["contractVersion"] == "2.0"


def test_catalog_rejects_unknown_scenario(fixtures_dir) -> None:
    with pytest.raises(EmulatorError):
        EmulatorCatalog(fixtures_dir).list_batches("../accounting")


def test_http_source_downloads_via_injected_fetch() -> None:
    ping = json.dumps({"status": "ok", "kind": "emulator", "live_1c": False}).encode()
    listing = json.dumps({"items": [{"exchange_id": "EX-TEST-001"}]}).encode()
    packet = json.dumps(f.batch()).encode()

    def fetch(url: str) -> bytes:
        if url.endswith("/hs/exchange/ping"):
            return ping
        if "/hs/exchange/batches?" in url:
            return listing
        if "/hs/exchange/batches/EX-TEST-001" in url:
            return packet
        raise AssertionError(url)

    source = HttpExchangeSource("http://127.0.0.1:8001", fetch=fetch)
    batches = source.load_batches()
    assert len(batches) == 1
    assert batches[0].exchange_id == "EX-TEST-001"
