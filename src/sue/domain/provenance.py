"""Происхождение показателей.

Каждый показатель СУЭ помечается источником, чтобы в отчёте было видно,
что взято из учётных данных, что смоделировано и что получено расчётом.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

ACCOUNTING = "accounting"
MODELED = "modeled"
DERIVED = "derived"
ACCOUNTING_MODELED = "accounting+modeled"

SOURCES = (ACCOUNTING, MODELED, DERIVED, ACCOUNTING_MODELED)


@dataclass(frozen=True)
class ProvenanceValue:
    """Значение показателя вместе с источником и пояснением методики."""

    value: Decimal
    source: str
    note: str = ""
    unit: str = "RUB"

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError(f"Неизвестное происхождение показателя: {self.source!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": float(self.value),
            "source": self.source,
            "note": self.note,
            "unit": self.unit,
        }
