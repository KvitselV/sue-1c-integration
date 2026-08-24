"""Генератор пакетов обмена формата 1С:Розница.

Данные синтетические и полностью воспроизводимые: при одинаковом ``seed`` результат
побитово совпадает. Суммы формируются в копейках целыми числами, поэтому контрольные
суммы в манифесте совпадают с итогами загрузки в СУЭ до копейки.

Документ соответствует «Отчёту о розничных продажах» 1С:Розница — сводке по кассовой
смене, а не отдельному чеку. Отсюда порядок величин: несколько смен в день на точку.

Порядок формирования строки важен для правдоподобия: сначала определяется количество
(целое для штучных товаров, кратное 50 г для весовых), и только затем сумма считается
как «количество × цена − скидка». Обратный порядок (количество = сумма / цена) даёт
значения вида «33.7 упаковки сыра».
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sue.adapter_1c.base import CONTRACT_VERSION
from sue.datagen.catalog import PRODUCTS, STORES, ProductDef, StoreDef

DISCLAIMER = (
    "Синтетические данные, структурно совместимые с контрактом обмена 1С:Розница. "
    "Не являются выгрузкой реальной информационной базы."
)

#: Единицы, продаваемые целым числом: дробная «упаковка» невозможна.
PIECE_UNITS = frozenset({"шт", "уп"})

#: Шаг весового количества в граммах — точность торговых весов.
WEIGHT_STEP_MILLI = 50


@dataclass(frozen=True)
class ScenarioSpec:
    """Параметры сценария генерации."""

    name: str
    description: str
    seed: int = 42
    end_week: date = date(2026, 5, 25)
    store_refs: tuple[str, ...] | None = None
    weeks_override: int | None = None
    docs_per_week: tuple[int, int] = (5, 9)
    lines_per_doc: tuple[int, int] = (2, 5)
    cost_coverage: float = 0.8
    return_rate: float = 0.03
    #: Доля документов, в которых применена скидка.
    discount_share: float = 0.25
    discount_range: tuple[float, float] = (0.05, 0.15)
    #: Доля недель без продаж (закрытая точка, ремонт) — данные не бывают идеальными.
    closed_week_rate: float = 0.0
    split_by_month: bool = True
    expectations: dict[str, Any] = field(default_factory=dict)


@dataclass
class GeneratedScenario:
    spec: ScenarioSpec
    files: list[dict[str, Any]]
    totals: dict[str, int]

    def to_manifest_entry(self) -> dict[str, Any]:
        spec = asdict(self.spec)
        spec["end_week"] = self.spec.end_week.isoformat()
        return {
            "scenario": self.spec.name,
            "description": self.spec.description,
            "spec": spec,
            "files": self.files,
            "control_totals": self.totals,
            "expectations": self.spec.expectations,
        }


def _week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _stores(spec: ScenarioSpec) -> list[StoreDef]:
    selected = [s for s in STORES if spec.store_refs is None or s.ref in spec.store_refs]
    if spec.weeks_override is not None:
        selected = [StoreDef(**{**asdict(s), "weeks": spec.weeks_override}) for s in selected]
    return selected


def _weighted_products(rng: random.Random, count: int) -> list[ProductDef]:
    weights = [p.popularity for p in PRODUCTS]
    return rng.choices(list(PRODUCTS), weights=weights, k=count)


def _weekly_target_kopecks(store: StoreDef, week_index: int, rng: random.Random) -> int:
    seasonal = 1.0 + store.seasonal_amplitude * math.sin(2 * math.pi * (week_index % 52) / 52)
    intra_month = 1.0 + 0.06 * math.sin(2 * math.pi * (week_index % 4) / 4)
    trend = 1.0 + store.trend_per_week * week_index
    noise = 1.0 + rng.uniform(-store.noise, store.noise)
    return int(store.weekly_revenue * 100 * seasonal * intra_month * max(trend, 0.2) * noise)


def _money(kopecks: int) -> float:
    return round(kopecks / 100, 2)


def _quantity(milli: int) -> float:
    return round(milli / 1000, 3)


def _quantity_milli(product: ProductDef, target_kopecks: int, unit_kopecks: int) -> int:
    """Количество, кратное реальному шагу продажи, а не производное от суммы."""
    raw_milli = target_kopecks * 1000 / max(unit_kopecks, 1)
    if product.unit in PIECE_UNITS:
        return max(1, round(raw_milli / 1000)) * 1000
    return max(1, round(raw_milli / WEIGHT_STEP_MILLI)) * WEIGHT_STEP_MILLI


class ExchangeGenerator:
    def __init__(self, spec: ScenarioSpec) -> None:
        self.spec = spec
        self.rng = random.Random(spec.seed)
        self.stores = _stores(spec)
        self._doc_seq = 0
        self._line_seq = 0

    # --- построение документов ------------------------------------------------

    def _next_doc_ref(self) -> tuple[str, str]:
        self._doc_seq += 1
        return f"{self.spec.name}-doc-{self._doc_seq:06d}", f"ОРП-{self._doc_seq:06d}"

    def _next_line_ref(self) -> str:
        self._line_seq += 1
        return f"{self.spec.name}-line-{self._line_seq:07d}"

    def _pick_discount(self) -> float:
        if self.spec.discount_share <= 0 or self.rng.random() >= self.spec.discount_share:
            return 0.0
        return round(self.rng.uniform(*self.spec.discount_range), 2)

    def _build_lines(self, target_kopecks: int, discount: float) -> list[dict[str, Any]]:
        count = self.rng.randint(*self.spec.lines_per_doc)
        products = _weighted_products(self.rng, count)

        shares = [self.rng.uniform(0.6, 1.4) for _ in range(count)]
        total_share = sum(shares)
        lines: list[dict[str, Any]] = []

        for product, share in zip(products, shares, strict=True):
            unit_kopecks = round(product.price * 100)
            line_target = max(int(target_kopecks * share / total_share), unit_kopecks)

            quantity_milli = _quantity_milli(product, line_target, unit_kopecks)
            gross_kopecks = round(unit_kopecks * quantity_milli / 1000)
            discount_kopecks = round(gross_kopecks * discount)
            amount_kopecks = max(gross_kopecks - discount_kopecks, 1)

            line: dict[str, Any] = {
                "ref": self._next_line_ref(),
                "productRef": product.ref,
                "quantity": _quantity(quantity_milli),
                "amount": _money(amount_kopecks),
            }
            if discount_kopecks:
                line["discountAmount"] = _money(discount_kopecks)
            if self.rng.random() < self.spec.cost_coverage:
                jitter = self.rng.uniform(0.97, 1.03)
                line["costAmount"] = _money(round(gross_kopecks * product.cost_ratio * jitter))
            lines.append(line)
        return lines

    def _build_documents(self) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        end = _week_monday(self.spec.end_week)

        for store in self.stores:
            for week_index in range(store.weeks):
                week_start = end - timedelta(days=7 * (store.weeks - 1 - week_index))
                target = _weekly_target_kopecks(store, week_index, self.rng)

                # Неделя без продаж: точка закрыта. Приёмник обязан корректно
                # обработать разрыв в ряду, а не «не заметить» его.
                if self.spec.closed_week_rate and self.rng.random() < self.spec.closed_week_rate:
                    continue

                doc_count = self.rng.randint(*self.spec.docs_per_week)
                remaining = target

                for i in range(doc_count):
                    doc_target = (
                        remaining if i == doc_count - 1 else int(remaining / (doc_count - i))
                    )
                    remaining -= doc_target
                    ref, number = self._next_doc_ref()
                    documents.append(
                        {
                            "ref": ref,
                            "number": number,
                            "date": (
                                week_start + timedelta(days=self.rng.randint(0, 6))
                            ).isoformat(),
                            "storeRef": store.ref,
                            "documentType": "sale",
                            "lines": self._build_lines(
                                max(doc_target, 10_000), self._pick_discount()
                            ),
                        }
                    )

                if self.spec.return_rate > 0 and self.rng.random() < self.spec.return_rate * 4:
                    documents.append(self._build_return(store, week_start, target))

        documents.sort(key=lambda d: (d["date"], d["ref"]))
        return documents

    def _build_return(self, store: StoreDef, week_start: date, week_target: int) -> dict[str, Any]:
        ref, number = self._next_doc_ref()
        target = max(int(week_target * self.rng.uniform(0.01, self.spec.return_rate * 2)), 5_000)
        return {
            "ref": ref,
            "number": number.replace("ОРП", "ВЗВ"),
            "date": (week_start + timedelta(days=self.rng.randint(0, 6))).isoformat(),
            "storeRef": store.ref,
            "documentType": "return",
            # Возврат оформляется по цене продажи, скидка в нём не применяется повторно.
            "lines": self._build_lines(target, 0.0),
        }

    # --- сборка пакетов -------------------------------------------------------

    def _meta(self, index: int, total: int, documents: list[dict[str, Any]]) -> dict[str, Any]:
        dates = [d["date"] for d in documents]
        return {
            "contractVersion": CONTRACT_VERSION,
            "exchangeId": f"EX-{self.spec.name.upper()}-{index:03d}",
            "sourceSystem": "1C:Retail",
            "configuration": "1С:Розница 3.0 (контракт файлового обмена)",
            "exportedAt": datetime(2026, 5, 26, 10, 0, 0).isoformat() + "Z",
            "currency": "RUB",
            "periodFrom": min(dates),
            "periodTo": max(dates),
            "comment": f"{DISCLAIMER} Пакет {index} из {total}, сценарий «{self.spec.name}».",
        }

    def _catalog_payload(self) -> dict[str, Any]:
        return {
            "stores": [
                {
                    "ref": s.ref,
                    "code": s.code,
                    "name": s.name,
                    "city": s.city,
                    "format": s.store_format,
                    "isActive": True,
                }
                for s in self.stores
            ],
            "products": [
                {
                    "ref": p.ref,
                    "sku": p.sku,
                    "name": p.name,
                    "category": p.category,
                    "unit": p.unit,
                }
                for p in PRODUCTS
            ],
        }

    def build_batches(self) -> list[dict[str, Any]]:
        documents = self._build_documents()
        groups: list[list[dict[str, Any]]] = []

        if self.spec.split_by_month:
            by_month: dict[str, list[dict[str, Any]]] = {}
            for doc in documents:
                by_month.setdefault(doc["date"][:7], []).append(doc)
            groups = [by_month[key] for key in sorted(by_month)]
        else:
            groups = [documents]

        catalog = self._catalog_payload()
        return [
            {**self._envelope(i + 1, len(groups), group, catalog)} for i, group in enumerate(groups)
        ]

    def _envelope(
        self, index: int, total: int, documents: list[dict[str, Any]], catalog: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "meta": self._meta(index, total, documents),
            "stores": catalog["stores"],
            "products": catalog["products"],
            "saleDocuments": documents,
        }


def totals_of(batches: list[dict[str, Any]]) -> dict[str, int]:
    """Контрольные суммы в минимальных единицах — для сверки после загрузки."""
    revenue = quantity = cost = lines = documents = 0
    store_refs: set[str] = set()
    product_refs: set[str] = set()

    for batch in batches:
        store_refs.update(s["ref"] for s in batch["stores"])
        product_refs.update(p["ref"] for p in batch["products"])
        for doc in batch["saleDocuments"]:
            documents += 1
            sign = -1 if doc.get("documentType") == "return" else 1
            for line in doc["lines"]:
                lines += 1
                revenue += sign * round(line["amount"] * 100)
                quantity += sign * round(line["quantity"] * 1000)
                if "costAmount" in line:
                    cost += sign * round(line["costAmount"] * 100)

    return {
        "stores": len(store_refs),
        "products": len(product_refs),
        "documents": documents,
        "lines": lines,
        "revenue_kopecks": revenue,
        "quantity_milli": quantity,
        "cost_accounting_kopecks": cost,
    }


def write_scenario(spec: ScenarioSpec, out_dir: Path) -> GeneratedScenario:
    batches = ExchangeGenerator(spec).build_batches()
    target = out_dir / spec.name
    target.mkdir(parents=True, exist_ok=True)
    for stale in target.glob("*.json"):
        stale.unlink()

    files: list[dict[str, Any]] = []
    for i, batch in enumerate(batches, start=1):
        path = target / f"exchange_{spec.name}_{i:03d}.json"
        raw = json.dumps(batch, ensure_ascii=False, indent=2).encode("utf-8")
        path.write_bytes(raw)
        files.append(
            {
                "file": path.relative_to(out_dir).as_posix(),
                "documents": len(batch["saleDocuments"]),
                "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )

    return GeneratedScenario(spec=spec, files=files, totals=totals_of(batches))
