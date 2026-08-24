"""Сценарии генерации данных.

Каждый сценарий проверяет отдельное поведение системы, и это зафиксировано в поле
``expectations``: манифест служит одновременно описанием данных и ожидаемым
результатом их обработки, на который опираются тесты.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sue import __version__
from sue.adapter_1c.base import CONTRACT_VERSION
from sue.datagen.catalog import COST_RATIO_BY_CATEGORY, OVERHEAD_RATE
from sue.datagen.generator import (
    DISCLAIMER,
    GeneratedScenario,
    ScenarioSpec,
    write_scenario,
)

SCENARIOS: dict[str, ScenarioSpec] = {
    "accounting": ScenarioSpec(
        name="accounting",
        description=(
            "Основной набор: шесть торговых точек, полтора года недельной истории, "
            "себестоимость выгружена для большинства строк, есть возвраты."
        ),
        seed=42,
        cost_coverage=0.8,
        return_rate=0.03,
        closed_week_rate=0.03,
        expectations={
            "cost_source": "accounting+modeled",
        },
    ),
    "no_cost": ScenarioSpec(
        name="no_cost",
        description=(
            "Выгрузка без себестоимости: проверяет, что показатель помечается как "
            "смоделированный, а не выдаётся за учётный."
        ),
        seed=1042,
        store_refs=("store-01", "store-02"),
        weeks_override=40,
        cost_coverage=0.0,
        return_rate=0.0,
        discount_share=0.0,
        expectations={"cost_source": "modeled", "cost_modeled_share_pct": 100.0},
    ),
    "short_history": ScenarioSpec(
        name="short_history",
        description=(
            "Короткая история (8 недель): новая точка с малым объёмом документов — "
            "проверка расчёта рентабельности на неполном периоде."
        ),
        seed=2042,
        store_refs=("store-04",),
        weeks_override=8,
        cost_coverage=1.0,
        return_rate=0.0,
        discount_share=0.0,
        split_by_month=False,
        expectations={"stores": 1, "weeks": 8},
    ),
    "returns_heavy": ScenarioSpec(
        name="returns_heavy",
        description=(
            "Повышенная доля возвратов: проверяет, что выручка считается за вычетом "
            "возвратов, а не по сумме реализации."
        ),
        seed=3042,
        store_refs=("store-03",),
        weeks_override=52,
        cost_coverage=0.9,
        return_rate=0.15,
        expectations={"returns_present": True},
    ),
    "large": ScenarioSpec(
        name="large",
        description=(
            "Объёмный набор для проверки производительности загрузки и агрегации "
            "(генерируется по флагу --with-large)."
        ),
        seed=4042,
        weeks_override=156,
        docs_per_week=(24, 32),
        lines_per_doc=(3, 7),
        cost_coverage=0.7,
        return_rate=0.04,
        closed_week_rate=0.02,
        expectations={"performance_case": True},
    ),
}

DEFAULT_SCENARIOS = ("accounting", "no_cost", "short_history", "returns_heavy")

_BASE_VALID_BATCH: dict[str, Any] = {
    "meta": {
        "contractVersion": CONTRACT_VERSION,
        "exchangeId": "EX-INVALID-DEMO",
        "sourceSystem": "1C:Retail",
        "configuration": "1С:Розница 3.0 (контракт файлового обмена)",
        "exportedAt": "2026-01-05T10:00:00Z",
    },
    "stores": [{"ref": "s1", "code": "TT-X", "name": "ТТ Демонстрационная", "city": "Казань"}],
    "products": [
        {"ref": "p1", "sku": "SKU-1", "name": "Товар 1", "category": "Прочее", "unit": "шт"}
    ],
    "saleDocuments": [
        {
            "ref": "d1",
            "number": "ОРП-000001",
            "date": "2026-01-05",
            "storeRef": "s1",
            "documentType": "sale",
            "lines": [{"ref": "l1", "productRef": "p1", "quantity": 2.0, "amount": 200.0}],
        }
    ],
}


def _deep_copy() -> dict[str, Any]:
    copied: dict[str, Any] = json.loads(json.dumps(_BASE_VALID_BATCH))
    return copied


def invalid_examples() -> dict[str, tuple[dict[str, Any], str]]:
    """Наборы с одним нарушением каждый — по одному на проверяемое правило."""
    examples: dict[str, tuple[dict[str, Any], str]] = {}

    batch = _deep_copy()
    batch["saleDocuments"][0]["storeRef"] = "UNKNOWN"
    examples["unknown_store_ref"] = (batch, "Ссылка на отсутствующую торговую точку")

    batch = _deep_copy()
    batch["saleDocuments"][0]["lines"][0]["productRef"] = "UNKNOWN"
    examples["unknown_product_ref"] = (batch, "Ссылка на отсутствующую номенклатуру")

    batch = _deep_copy()
    batch["meta"]["contractVersion"] = "1.0"
    examples["unsupported_contract_version"] = (batch, "Неподдерживаемая версия контракта")

    batch = _deep_copy()
    batch["saleDocuments"][0]["lines"][0]["amount"] = -200.0
    examples["negative_amount"] = (batch, "Отрицательная сумма в строке документа")

    batch = _deep_copy()
    batch["saleDocuments"][0]["lines"][0]["amount"] = 200.12345
    examples["excess_precision"] = (batch, "Точность суммы больше двух знаков")

    batch = _deep_copy()
    batch["saleDocuments"].append(json.loads(json.dumps(batch["saleDocuments"][0])))
    examples["duplicate_document_ref"] = (batch, "Дубль ref документа внутри пакета")

    batch = _deep_copy()
    batch["meta"]["periodFrom"] = "2026-02-01"
    batch["meta"]["periodTo"] = "2026-02-28"
    examples["date_out_of_period"] = (batch, "Дата документа вне заявленного периода")

    batch = _deep_copy()
    del batch["meta"]["exchangeId"]
    examples["missing_required_field"] = (batch, "Отсутствует обязательное поле meta/exchangeId")

    batch = _deep_copy()
    batch["saleDocuments"][0]["documentType"] = "writeoff"
    examples["unknown_document_type"] = (batch, "Недопустимый тип документа")

    batch = _deep_copy()
    batch["saleDocuments"][0]["lines"][0]["quantity"] = -2.0
    examples["negative_quantity"] = (batch, "Отрицательное количество в строке документа")

    batch = _deep_copy()
    batch["saleDocuments"][0]["lines"][0]["quantity"] = 0
    examples["zero_quantity_with_amount"] = (batch, "Нулевое количество при ненулевой сумме")

    batch = _deep_copy()
    batch["saleDocuments"][0]["lines"].append(
        json.loads(json.dumps(batch["saleDocuments"][0]["lines"][0]))
    )
    examples["duplicate_line_ref"] = (batch, "Дубль ref строки внутри пакета")

    batch = _deep_copy()
    batch["saleDocuments"][0]["lines"][0]["vatRate"] = 20
    examples["unexpected_field"] = (batch, "Лишнее поле в строке (additionalProperties: false)")

    batch = _deep_copy()
    batch["saleDocuments"][0]["date"] = "05.01.2026"
    examples["invalid_date_format"] = (batch, "Дата не в формате ISO 8601")

    batch = _deep_copy()
    batch["saleDocuments"] = []
    examples["empty_sale_documents"] = (batch, "Пустой массив документов реализации")

    batch = _deep_copy()
    del batch["meta"]
    examples["missing_meta"] = (batch, "Отсутствует блок meta целиком")

    return examples


def write_invalid_examples(out_dir: Path) -> list[dict[str, Any]]:
    target = out_dir / "invalid"
    target.mkdir(parents=True, exist_ok=True)
    for stale in target.glob("*.json"):
        stale.unlink()

    entries: list[dict[str, Any]] = []
    for key, (batch, description) in sorted(invalid_examples().items()):
        path = target / f"invalid_{key}.json"
        raw = json.dumps(batch, ensure_ascii=False, indent=2).encode("utf-8")
        path.write_bytes(raw)
        entries.append(
            {
                "file": path.relative_to(out_dir).as_posix(),
                "violation": key,
                "description": description,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    return entries


def write_modeled_params(out_dir: Path) -> dict[str, Any]:
    """Параметры, которых нет в учётных данных. Вынесены в файлы, а не в код."""
    target = out_dir / "modeled"
    target.mkdir(parents=True, exist_ok=True)

    overhead = {
        "overhead_rate": OVERHEAD_RATE,
        "description": (
            "Доля накладных расходов от выручки торговой точки. МОДЕЛИРУЕМЫЙ параметр: "
            "в выгрузке 1С:Розница нет аллокации аренды, ФОТ и общехозяйственных "
            "расходов по торговым точкам."
        ),
        "sensitivity": {"delta": [-0.03, 0.0, 0.03]},
    }
    cost = {
        "description": (
            "cost_ratio — отношение себестоимости к выручке по категории. Применяется "
            "ТОЛЬКО к строкам, в которых поле costAmount отсутствует."
        ),
        "cost_ratio_by_category": COST_RATIO_BY_CATEGORY,
    }

    (target / "overhead_params.json").write_text(
        json.dumps(overhead, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (target / "cost_markup_by_category.json").write_text(
        json.dumps(cost, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"overhead_params": overhead, "cost_markup_by_category": cost}


def generate(
    out_dir: Path,
    names: tuple[str, ...] = DEFAULT_SCENARIOS,
    *,
    with_invalid: bool = True,
) -> dict[str, Any]:
    """Сгенерировать выбранные сценарии и записать манифест."""
    out_dir.mkdir(parents=True, exist_ok=True)

    generated: list[GeneratedScenario] = []
    for name in names:
        if name not in SCENARIOS:
            raise KeyError(f"Неизвестный сценарий: {name}. Доступны: {sorted(SCENARIOS)}")
        generated.append(write_scenario(SCENARIOS[name], out_dir))

    manifest: dict[str, Any] = {
        "generator": {
            "package": "sue.datagen",
            "app_version": __version__,
            "contract_version": CONTRACT_VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "reproducible": "Одинаковый seed даёт побитово одинаковые файлы",
        },
        "disclaimer": DISCLAIMER,
        "modeled_params": write_modeled_params(out_dir),
        "scenarios": [g.to_manifest_entry() for g in generated],
    }
    if with_invalid:
        manifest["invalid_examples"] = write_invalid_examples(out_dir)

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest
