"""Табличное представление пакета обмена.

Читает JSON-файлы контракта 2.0 из каталога фикстур. Это не справочники СУЭ
и не живая база 1С — так выглядит выгрузка, которую принимает загрузка.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sue.adapter_1c.base import SourceError
from sue.emulator.catalog import EmulatorCatalog, EmulatorError
from sue.i18n import ru_doc_type

LINE_LIMIT = 200

SCENARIO_RU = {
    "accounting": "основной набор",
    "no_cost": "без себестоимости",
    "short_history": "короткая история",
    "returns_heavy": "много возвратов",
    "invalid": "нарушения контракта",
    "main": "основной набор",
    "short": "короткая история",
    "nocost": "без себестоимости",
}


def _scenario_label(name: str) -> str:
    title = SCENARIO_RU.get(name)
    return f"{name} — {title}" if title else name


def _sum_amount(lines: list[dict[str, Any]]) -> float:
    total = 0.0
    for line in lines:
        try:
            total += float(line.get("amount") or 0)
        except (TypeError, ValueError):
            continue
    return total


def exchange_preview(
    fixtures_dir: Path,
    scenario: str | None = None,
    exchange_id: str | None = None,
) -> dict[str, Any]:
    catalog = EmulatorCatalog(fixtures_dir)
    try:
        scenarios = catalog.scenarios()
    except OSError:
        scenarios = []

    if not scenarios:
        return {
            "scenarios": [],
            "batches": [],
            "scenario": "",
            "exchange_id": "",
            "meta": {},
            "stores": [],
            "products": [],
            "documents": [],
            "lines": [],
            "lines_total": 0,
            "lines_shown": 0,
            "error": "В каталоге обмена нет сценариев.",
        }

    chosen_scenario = (
        scenario
        if scenario in scenarios
        else ("accounting" if "accounting" in scenarios else scenarios[0])
    )
    try:
        batches = catalog.list_batches(chosen_scenario)
    except EmulatorError as exc:
        return {
            "scenarios": [{"id": name, "label": _scenario_label(name)} for name in scenarios],
            "batches": [],
            "scenario": chosen_scenario,
            "exchange_id": "",
            "meta": {},
            "stores": [],
            "products": [],
            "documents": [],
            "lines": [],
            "lines_total": 0,
            "lines_shown": 0,
            "error": str(exc),
        }

    batch_ids = [item.exchange_id for item in batches]
    chosen_id = exchange_id if exchange_id in batch_ids else (batch_ids[0] if batch_ids else "")

    empty = {
        "scenarios": [{"id": name, "label": _scenario_label(name)} for name in scenarios],
        "batches": [
            {
                "exchange_id": item.exchange_id,
                "filename": item.filename,
                "documents": item.documents,
                "period_from": item.period_from,
                "period_to": item.period_to,
            }
            for item in batches
        ],
        "scenario": chosen_scenario,
        "exchange_id": chosen_id,
        "meta": {},
        "stores": [],
        "products": [],
        "documents": [],
        "lines": [],
        "lines_total": 0,
        "lines_shown": 0,
        "error": None,
    }
    if not chosen_id:
        empty["error"] = "В сценарии нет пакетов обмена."
        return empty

    try:
        batch = catalog.get_batch(chosen_scenario, chosen_id)
    except (EmulatorError, SourceError) as exc:
        empty["error"] = str(exc)
        return empty

    payload = batch.payload
    stores = []
    for raw in payload.get("stores") or []:
        if not isinstance(raw, dict):
            continue
        stores.append(
            {
                "ref": raw.get("ref") or "—",
                "code": raw.get("code") or "—",
                "name": raw.get("name") or "—",
                "city": raw.get("city") or "—",
                "format": raw.get("format") or "—",
                "active": "да" if raw.get("isActive") else "нет",
            }
        )
    store_by_ref = {row["ref"]: row for row in stores}

    products = []
    for raw in payload.get("products") or []:
        if not isinstance(raw, dict):
            continue
        products.append(
            {
                "ref": raw.get("ref") or "—",
                "sku": raw.get("sku") or "—",
                "name": raw.get("name") or "—",
                "category": raw.get("category") or "—",
                "unit": raw.get("unit") or "—",
            }
        )
    product_by_ref = {row["ref"]: row for row in products}

    documents = []
    lines: list[dict[str, Any]] = []
    for raw in payload.get("saleDocuments") or []:
        if not isinstance(raw, dict):
            continue
        doc_lines = [line for line in (raw.get("lines") or []) if isinstance(line, dict)]
        store = store_by_ref.get(str(raw.get("storeRef") or ""), {})
        documents.append(
            {
                "date": raw.get("date") or "—",
                "doc_type": ru_doc_type(str(raw.get("documentType") or "")),
                "number": raw.get("number") or "—",
                "store": store.get("code") or raw.get("storeRef") or "—",
                "store_name": store.get("name") or "—",
                "lines": len(doc_lines),
                "amount": _sum_amount(doc_lines),
            }
        )
        for line in doc_lines:
            product = product_by_ref.get(str(line.get("productRef") or ""), {})
            lines.append(
                {
                    "doc_number": raw.get("number") or "—",
                    "sku": product.get("sku") or line.get("productRef") or "—",
                    "name": product.get("name") or "—",
                    "quantity": line.get("quantity"),
                    "amount": line.get("amount"),
                    "discount": line.get("discountAmount"),
                    "cost": line.get("costAmount"),
                }
            )

    meta = batch.meta if isinstance(batch.meta, dict) else {}
    return {
        **empty,
        "meta": meta,
        "stores": stores,
        "products": products,
        "documents": documents,
        "lines": lines[:LINE_LIMIT],
        "lines_total": len(lines),
        "lines_shown": min(LINE_LIMIT, len(lines)),
        "error": None,
    }
