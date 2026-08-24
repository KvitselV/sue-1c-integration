"""Каталог пакетов, которые отдаёт эмулятор выгрузки 1С.

Это не информационная база: эмулятор читает JSON-пакеты контракта 2.0
(демонстрационные сценарии) и публикует их тем же составом, что ушла бы
типовая выгрузка из 1С:Розница.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sue.adapter_1c.base import Batch, OneCSource

_SCENARIO_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_HIDDEN = frozenset({"modeled"})


class EmulatorError(ValueError):
    """Пакет или сценарий в каталоге эмулятора не найден."""


@dataclass(frozen=True)
class BatchInfo:
    exchange_id: str
    scenario: str
    filename: str
    period_from: str | None
    period_to: str | None
    documents: int
    size_bytes: int


class EmulatorCatalog:
    def __init__(self, fixtures_dir: Path) -> None:
        self.fixtures_dir = fixtures_dir

    def scenarios(self) -> list[str]:
        found = []
        for path in sorted(self.fixtures_dir.iterdir()):
            if path.is_dir() and path.name not in _HIDDEN and _SCENARIO_NAME.match(path.name):
                found.append(path.name)
        return found

    def list_batches(self, scenario: str = "accounting") -> list[BatchInfo]:
        directory = self._scenario_dir(scenario)
        items: list[BatchInfo] = []
        for file in sorted(p for p in directory.glob("*.json") if p.is_file()):
            payload = _read_json(file)
            raw_meta = payload.get("meta")
            meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
            exchange_id = str(meta.get("exchangeId") or file.stem)
            documents = payload.get("saleDocuments")
            items.append(
                BatchInfo(
                    exchange_id=exchange_id,
                    scenario=scenario,
                    filename=file.name,
                    period_from=meta.get("periodFrom"),
                    period_to=meta.get("periodTo"),
                    documents=len(documents) if isinstance(documents, list) else 0,
                    size_bytes=file.stat().st_size,
                )
            )
        return items

    def get_raw(self, scenario: str, exchange_id: str) -> bytes:
        for file in self._scenario_dir(scenario).glob("*.json"):
            if not file.is_file():
                continue
            raw = file.read_bytes()
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            meta = payload.get("meta") if isinstance(payload, dict) else None
            current = None
            if isinstance(meta, dict) and meta.get("exchangeId"):
                current = str(meta["exchangeId"])
            if current == exchange_id or file.stem == exchange_id or file.name == exchange_id:
                return raw
        raise EmulatorError(f"Пакет {exchange_id} в сценарии {scenario} не найден")

    def get_batch(self, scenario: str, exchange_id: str) -> Batch:
        raw = self.get_raw(scenario, exchange_id)
        return Batch.from_bytes(f"emulator:{scenario}/{exchange_id}", raw)

    def iter_batches(self, scenario: str, exchange_id: str | None = None) -> list[Batch]:
        if exchange_id:
            return [self.get_batch(scenario, exchange_id)]
        return [self.get_batch(scenario, item.exchange_id) for item in self.list_batches(scenario)]

    def _scenario_dir(self, scenario: str) -> Path:
        if not _SCENARIO_NAME.match(scenario):
            raise EmulatorError(f"Неизвестный сценарий выгрузки: {scenario}")
        directory = self.fixtures_dir / scenario
        if not directory.is_dir():
            raise EmulatorError(f"Сценарий {scenario} отсутствует в каталоге эмулятора")
        return directory


class CatalogSource(OneCSource):
    """Пакеты, уже прочитанные из каталога эмулятора."""

    def __init__(self, batches: list[Batch], label: str = "emulator") -> None:
        self.batches = batches
        self.label = label

    def iter_batches(self) -> Iterator[Batch]:
        yield from self.batches


def _read_json(file: Path) -> dict[str, Any]:
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
