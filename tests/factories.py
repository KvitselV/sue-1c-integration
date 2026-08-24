"""Фабрики минимальных валидных пакетов обмена для unit-тестов.

Позволяют проверять поведение на точно заданных числах, а не на сгенерированном
массиве: ожидаемый результат в таких тестах считается вручную.
"""

from __future__ import annotations

from typing import Any

from sue.adapter_1c.base import CONTRACT_VERSION


def store(ref: str = "s1", code: str = "TT-01", name: str = "ТТ Тестовая") -> dict[str, Any]:
    return {"ref": ref, "code": code, "name": name, "city": "Казань", "isActive": True}


def product(
    ref: str = "p1",
    sku: str = "SKU-1",
    name: str = "Товар 1",
    category: str = "Прочее",
) -> dict[str, Any]:
    return {"ref": ref, "sku": sku, "name": name, "category": category, "unit": "шт"}


def line(
    ref: str,
    amount: float,
    quantity: float = 1.0,
    cost: float | None = None,
    product_ref: str = "p1",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ref": ref,
        "productRef": product_ref,
        "quantity": quantity,
        "amount": amount,
    }
    if cost is not None:
        payload["costAmount"] = cost
    return payload


def document(
    ref: str = "d1",
    date: str = "2026-01-05",
    lines: list[dict[str, Any]] | None = None,
    doc_type: str = "sale",
    store_ref: str = "s1",
) -> dict[str, Any]:
    return {
        "ref": ref,
        "number": ref.upper(),
        "date": date,
        "storeRef": store_ref,
        "documentType": doc_type,
        "lines": lines if lines is not None else [line("l1", 100.0)],
    }


def batch(
    documents: list[dict[str, Any]] | None = None,
    stores: list[dict[str, Any]] | None = None,
    products: list[dict[str, Any]] | None = None,
    exchange_id: str = "EX-TEST-001",
    **meta: Any,
) -> dict[str, Any]:
    return {
        "meta": {
            "contractVersion": CONTRACT_VERSION,
            "exchangeId": exchange_id,
            "sourceSystem": "1C:Retail",
            "configuration": "1С:Розница 3.0 (тест)",
            "exportedAt": "2026-01-06T10:00:00Z",
            **meta,
        },
        "stores": stores if stores is not None else [store()],
        "products": products if products is not None else [product()],
        "saleDocuments": documents if documents is not None else [document()],
    }


def weekly_documents(
    weeks: int,
    amount: float = 1000.0,
    start: str = "2026-01-05",
    store_ref: str = "s1",
) -> list[dict[str, Any]]:
    """По одному документу на неделю — управляемая длина ряда для тестов."""
    from datetime import date, timedelta

    first = date.fromisoformat(start)
    return [
        document(
            ref=f"d{i:03d}",
            date=(first + timedelta(days=7 * i)).isoformat(),
            lines=[line(f"l{i:03d}", amount)],
            store_ref=store_ref,
        )
        for i in range(weeks)
    ]
