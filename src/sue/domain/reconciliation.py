"""Сверка «источник ↔ СУЭ».

Проверяет, что данные не потерялись и не исказились при загрузке: контрольные суммы
считаются по исходным файлам обмена и по содержимому БД в одних и тех же единицах
(копейки, тысячные доли количества), поэтому допустимое расхождение — ровно ноль.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sue.adapter_1c import Batch, FileExchangeSource
from sue.db.models import DOC_TYPE_RETURN, Product, SaleDocument, SaleLine, Store
from sue.money import from_kopecks, from_milli, to_kopecks, to_milli


@dataclass(frozen=True)
class Totals:
    """Контрольные суммы в минимальных единицах — сравниваются точно."""

    stores: int = 0
    products: int = 0
    documents: int = 0
    lines: int = 0
    revenue_kopecks: int = 0
    quantity_milli: int = 0
    cost_accounting_kopecks: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "stores": self.stores,
            "products": self.products,
            "documents": self.documents,
            "lines": self.lines,
            "revenue": float(from_kopecks(self.revenue_kopecks)),
            "quantity": float(from_milli(self.quantity_milli)),
            "cost_accounting": float(from_kopecks(self.cost_accounting_kopecks)),
        }

    def diff(self, other: Totals) -> dict[str, int]:
        return {
            "stores": self.stores - other.stores,
            "products": self.products - other.products,
            "documents": self.documents - other.documents,
            "lines": self.lines - other.lines,
            "revenue_kopecks": self.revenue_kopecks - other.revenue_kopecks,
            "quantity_milli": self.quantity_milli - other.quantity_milli,
            "cost_accounting_kopecks": (
                self.cost_accounting_kopecks - other.cost_accounting_kopecks
            ),
        }


def batch_totals(batch: Batch) -> Totals:
    payload = batch.payload
    revenue = 0
    quantity = 0
    cost = 0
    lines = 0

    for doc in payload.get("saleDocuments", []):
        sign = -1 if doc.get("documentType") == DOC_TYPE_RETURN else 1
        for line in doc.get("lines", []):
            revenue += sign * to_kopecks(line["amount"])
            quantity += sign * to_milli(line["quantity"])
            if line.get("costAmount") is not None:
                cost += sign * to_kopecks(line["costAmount"])
            lines += 1

    return Totals(
        stores=len(payload.get("stores", [])),
        products=len(payload.get("products", [])),
        documents=len(payload.get("saleDocuments", [])),
        lines=lines,
        revenue_kopecks=revenue,
        quantity_milli=quantity,
        cost_accounting_kopecks=cost,
    )


def source_totals(path: Path | str) -> Totals:
    """Контрольные суммы по всем файлам обмена в каталоге (справочники — по уникальным ref)."""
    store_refs: set[str] = set()
    product_refs: set[str] = set()
    doc_refs: set[str] = set()
    revenue = quantity = cost = lines = 0

    for batch in FileExchangeSource(path).iter_batches():
        totals = batch_totals(batch)
        revenue += totals.revenue_kopecks
        quantity += totals.quantity_milli
        cost += totals.cost_accounting_kopecks
        lines += totals.lines
        store_refs.update(s["ref"] for s in batch.payload.get("stores", []))
        product_refs.update(p["ref"] for p in batch.payload.get("products", []))
        doc_refs.update(d["ref"] for d in batch.payload.get("saleDocuments", []))

    return Totals(
        stores=len(store_refs),
        products=len(product_refs),
        documents=len(doc_refs),
        lines=lines,
        revenue_kopecks=revenue,
        quantity_milli=quantity,
        cost_accounting_kopecks=cost,
    )


def db_totals(db: Session) -> Totals:
    revenue, quantity, cost, lines = db.execute(
        select(
            func.coalesce(func.sum(SaleLine.revenue_kopecks), 0),
            func.coalesce(func.sum(SaleLine.quantity_milli), 0),
            func.coalesce(func.sum(func.coalesce(SaleLine.cost_accounting_kopecks, 0)), 0),
            func.count(SaleLine.id),
        )
    ).one()
    return Totals(
        stores=db.scalar(select(func.count(Store.id))) or 0,
        products=db.scalar(select(func.count(Product.id))) or 0,
        documents=db.scalar(select(func.count(SaleDocument.id))) or 0,
        lines=int(lines),
        revenue_kopecks=int(revenue),
        quantity_milli=int(quantity),
        cost_accounting_kopecks=int(cost),
    )


def reconcile(db: Session, source: Path | str, label: str | None = None) -> dict[str, Any]:
    """Отчёт сверки: суммы источника, суммы СУЭ и расхождение по каждой позиции.

    ``label`` подставляется вместо пути: в ответе API не должно быть абсолютных путей
    файловой системы сервера, к тому же они делают пример в документации
    невоспроизводимым на другой машине.
    """
    src = source_totals(source)
    dst = db_totals(db)
    diff = src.diff(dst)
    return {
        "source": label or str(source),
        "source_totals": src.to_dict(),
        "db_totals": dst.to_dict(),
        "diff_minor_units": diff,
        "matched": all(v == 0 for v in diff.values()),
    }


def reconciliation_totals(db: Session) -> dict[str, Any]:
    """Контрольные суммы в СУЭ (совместимый с прежним API формат)."""
    totals = db_totals(db)
    data = totals.to_dict()
    data["sale_lines"] = totals.lines
    data["revenue_sum"] = data["revenue"]
    data["quantity_sum"] = data["quantity"]
    return data


def revenue_decimal(kopecks: int) -> Decimal:
    return from_kopecks(kopecks)
