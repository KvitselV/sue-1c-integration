"""Валидация пакета обмена: JSON Schema + правила предметной области.

Проверка двухуровневая, потому что JSON Schema описывает форму документа, но не
может выразить связность ссылок между справочниками и документами, уникальность
идентификаторов и согласованность сумм внутри пакета.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from sue.adapter_1c.base import SUPPORTED_CONTRACT_VERSIONS

STAGE_SCHEMA = "validate_schema"
STAGE_RULES = "validate_rules"


@dataclass(frozen=True)
class ValidationIssue:
    """Одно нарушение контракта с адресом внутри документа."""

    stage: str
    location: str
    detail: str
    entity: str | None = None
    source_ref: str | None = None
    severity: str = "error"

    def __str__(self) -> str:
        return f"{self.location}: {self.detail}"


@lru_cache(maxsize=8)
def _load_schema(path: str) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as f:
        schema: dict[str, Any] = json.load(f)
    return schema


class ContractValidator:
    def __init__(self, schema_path: Path | str) -> None:
        self.schema_path = Path(schema_path)
        self.schema = _load_schema(str(self.schema_path))
        self.validator = Draft202012Validator(self.schema, format_checker=FormatChecker())

    # --- уровень 1: структура -------------------------------------------------

    def validate_schema(self, payload: dict[str, Any]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for error in sorted(self.validator.iter_errors(payload), key=lambda e: list(e.path)):
            location = "/".join(str(p) for p in error.path) or "<root>"
            issues.append(
                ValidationIssue(
                    stage=STAGE_SCHEMA,
                    location=location,
                    detail=error.message,
                    entity=str(error.path[0]) if error.path else "batch",
                )
            )
        return issues

    # --- уровень 2: правила предметной области --------------------------------

    def validate_rules(self, payload: dict[str, Any]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        issues.extend(self._check_contract_version(payload))
        issues.extend(self._check_duplicate_refs(payload))
        issues.extend(self._check_documents(payload))
        issues.extend(self._check_period(payload))
        return issues

    def _check_contract_version(self, payload: dict[str, Any]) -> list[ValidationIssue]:
        version = payload.get("meta", {}).get("contractVersion")
        if version in SUPPORTED_CONTRACT_VERSIONS:
            return []
        return [
            ValidationIssue(
                stage=STAGE_RULES,
                location="meta/contractVersion",
                entity="meta",
                detail=(
                    f"версия контракта {version!r} не поддерживается; "
                    f"допустимые: {sorted(SUPPORTED_CONTRACT_VERSIONS)}"
                ),
            )
        ]

    def _check_duplicate_refs(self, payload: dict[str, Any]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for key, entity in (("stores", "store"), ("products", "product")):
            counter = Counter(item.get("ref") for item in payload.get(key, []))
            for ref, count in counter.items():
                if count > 1:
                    issues.append(
                        ValidationIssue(
                            stage=STAGE_RULES,
                            location=f"{key}",
                            entity=entity,
                            source_ref=str(ref),
                            detail=f"ref встречается {count} раз в одном пакете",
                        )
                    )

        doc_counter = Counter(doc.get("ref") for doc in payload.get("saleDocuments", []))
        for ref, count in doc_counter.items():
            if count > 1:
                issues.append(
                    ValidationIssue(
                        stage=STAGE_RULES,
                        location="saleDocuments",
                        entity="saleDocument",
                        source_ref=str(ref),
                        detail=f"ref документа встречается {count} раз в одном пакете",
                    )
                )

        line_refs = Counter(
            line.get("ref")
            for doc in payload.get("saleDocuments", [])
            for line in doc.get("lines", [])
        )
        for ref, count in line_refs.items():
            if count > 1:
                issues.append(
                    ValidationIssue(
                        stage=STAGE_RULES,
                        location="saleDocuments/*/lines",
                        entity="saleLine",
                        source_ref=str(ref),
                        detail=f"ref строки встречается {count} раз в одном пакете",
                    )
                )
        return issues

    def _check_documents(self, payload: dict[str, Any]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        store_refs = {s.get("ref") for s in payload.get("stores", [])}
        product_refs = {p.get("ref") for p in payload.get("products", [])}

        for i, doc in enumerate(payload.get("saleDocuments", [])):
            doc_ref = doc.get("ref")
            base = f"saleDocuments/{i}"
            if doc.get("storeRef") not in store_refs:
                issues.append(
                    ValidationIssue(
                        stage=STAGE_RULES,
                        location=f"{base}/storeRef",
                        entity="saleDocument",
                        source_ref=doc_ref,
                        detail=f"неизвестный storeRef={doc.get('storeRef')!r}",
                    )
                )

            for j, line in enumerate(doc.get("lines", [])):
                loc = f"{base}/lines/{j}"
                line_ref = line.get("ref")
                if line.get("productRef") not in product_refs:
                    issues.append(
                        ValidationIssue(
                            stage=STAGE_RULES,
                            location=f"{loc}/productRef",
                            entity="saleLine",
                            source_ref=line_ref,
                            detail=f"неизвестный productRef={line.get('productRef')!r}",
                        )
                    )
                issues.extend(self._check_line_amounts(loc, line_ref, line))
        return issues

    def _check_line_amounts(
        self, loc: str, line_ref: str | None, line: dict[str, Any]
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        try:
            amount = Decimal(str(line.get("amount", 0)))
            quantity = Decimal(str(line.get("quantity", 0)))
        except Exception:  # значения уже проверены схемой; страховка от нечисловых типов
            return issues

        for field_name, value, max_scale in (
            ("amount", amount, 2),
            ("quantity", quantity, 3),
        ):
            exponent = value.as_tuple().exponent
            if isinstance(exponent, int) and exponent < -max_scale:
                issues.append(
                    ValidationIssue(
                        stage=STAGE_RULES,
                        location=f"{loc}/{field_name}",
                        entity="saleLine",
                        source_ref=line_ref,
                        detail=(
                            f"точность значения {value} превышает {max_scale} знака после запятой"
                        ),
                    )
                )

        if quantity == 0 and amount != 0:
            issues.append(
                ValidationIssue(
                    stage=STAGE_RULES,
                    location=loc,
                    entity="saleLine",
                    source_ref=line_ref,
                    detail="quantity=0 при amount≠0",
                )
            )

        cost = line.get("costAmount")
        if cost is not None:
            cost_dec = Decimal(str(cost))
            if cost_dec > amount * Decimal("3"):
                issues.append(
                    ValidationIssue(
                        stage=STAGE_RULES,
                        location=f"{loc}/costAmount",
                        entity="saleLine",
                        source_ref=line_ref,
                        severity="warning",
                        detail=(
                            f"себестоимость {cost_dec} более чем втрое превышает сумму {amount} — "
                            "вероятна ошибка выгрузки"
                        ),
                    )
                )
        return issues

    def _check_period(self, payload: dict[str, Any]) -> list[ValidationIssue]:
        meta = payload.get("meta", {})
        period_from = meta.get("periodFrom")
        period_to = meta.get("periodTo")
        if not period_from or not period_to:
            return []
        try:
            start = date.fromisoformat(period_from)
            end = date.fromisoformat(period_to)
        except ValueError:
            return []

        issues: list[ValidationIssue] = []
        if start > end:
            issues.append(
                ValidationIssue(
                    stage=STAGE_RULES,
                    location="meta/periodFrom",
                    entity="meta",
                    detail=f"periodFrom {start} позже periodTo {end}",
                )
            )
        for i, doc in enumerate(payload.get("saleDocuments", [])):
            try:
                doc_date = date.fromisoformat(str(doc.get("date")))
            except ValueError:
                continue
            if not (start <= doc_date <= end):
                issues.append(
                    ValidationIssue(
                        stage=STAGE_RULES,
                        location=f"saleDocuments/{i}/date",
                        entity="saleDocument",
                        source_ref=doc.get("ref"),
                        detail=f"дата {doc_date} вне заявленного периода {start}..{end}",
                    )
                )
        return issues

    # --- совместимость с прежним интерфейсом ---------------------------------

    def validate(self, payload: dict[str, Any]) -> list[str]:
        return [str(i) for i in self.validate_schema(payload)]

    def business_rules(self, payload: dict[str, Any]) -> list[str]:
        return [str(i) for i in self.validate_rules(payload)]
