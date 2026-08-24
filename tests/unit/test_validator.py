"""Валидация контракта обмена: структура и правила предметной области."""

from __future__ import annotations

import pytest

from sue.adapter_1c import ContractValidator
from sue.config import get_settings
from sue.datagen.scenarios import invalid_examples
from tests import factories as f


@pytest.fixture(scope="module")
def validator() -> ContractValidator:
    return ContractValidator(get_settings().schema_path)


def test_valid_batch_passes(validator: ContractValidator) -> None:
    payload = f.batch()
    assert validator.validate_schema(payload) == []
    assert validator.validate_rules(payload) == []


def test_unknown_store_ref_is_reported(validator: ContractValidator) -> None:
    payload = f.batch(documents=[f.document(store_ref="missing")])
    issues = validator.validate_rules(payload)
    assert any("storeRef" in i.location for i in issues)


def test_unknown_product_ref_is_reported(validator: ContractValidator) -> None:
    payload = f.batch(documents=[f.document(lines=[f.line("l1", 10.0, product_ref="nope")])])
    issues = validator.validate_rules(payload)
    assert any("productRef" in i.location for i in issues)


def test_unsupported_contract_version_is_rejected(validator: ContractValidator) -> None:
    payload = f.batch()
    payload["meta"]["contractVersion"] = "9.9"
    assert validator.validate_schema(payload)  # enum схемы тоже не пропустит


def test_duplicate_refs_within_batch_are_reported(validator: ContractValidator) -> None:
    doc = f.document(ref="d1")
    payload = f.batch(documents=[doc, dict(doc)])
    issues = validator.validate_rules(payload)
    assert any("ref документа встречается" in i.detail for i in issues)


def test_excess_precision_is_reported(validator: ContractValidator) -> None:
    payload = f.batch(documents=[f.document(lines=[f.line("l1", 10.12345)])])
    issues = validator.validate_rules(payload)
    assert any("точность значения" in i.detail for i in issues)


def test_negative_amount_is_rejected_by_schema(validator: ContractValidator) -> None:
    payload = f.batch(documents=[f.document(lines=[f.line("l1", -10.0)])])
    assert validator.validate_schema(payload)


def test_date_outside_declared_period_is_reported(validator: ContractValidator) -> None:
    payload = f.batch(periodFrom="2026-02-01", periodTo="2026-02-28")
    issues = validator.validate_rules(payload)
    assert any("вне заявленного периода" in i.detail for i in issues)


def test_suspicious_cost_is_warning_not_error(validator: ContractValidator) -> None:
    payload = f.batch(documents=[f.document(lines=[f.line("l1", 10.0, cost=100.0)])])
    issues = validator.validate_rules(payload)
    assert issues and all(i.severity == "warning" for i in issues)


@pytest.mark.parametrize("violation", sorted(invalid_examples()))
def test_every_generated_invalid_example_is_caught(
    validator: ContractValidator, violation: str
) -> None:
    """Каждый пример из сценария invalid должен отклоняться хотя бы одной проверкой."""
    payload, _ = invalid_examples()[violation]
    issues = validator.validate_schema(payload) or validator.validate_rules(payload)
    assert [i for i in issues if i.severity == "error"], violation
