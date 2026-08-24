"""Генератор синтетических данных формата контракта 1С:Розница."""

from sue.datagen.generator import (
    DISCLAIMER,
    ExchangeGenerator,
    ScenarioSpec,
    totals_of,
    write_scenario,
)
from sue.datagen.scenarios import DEFAULT_SCENARIOS, SCENARIOS, generate, invalid_examples

__all__ = [
    "DEFAULT_SCENARIOS",
    "DISCLAIMER",
    "SCENARIOS",
    "ExchangeGenerator",
    "ScenarioSpec",
    "generate",
    "invalid_examples",
    "totals_of",
    "write_scenario",
]
