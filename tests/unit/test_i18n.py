"""Русские подписи интерфейса.

Смысл тестов не в переводе как таком, а в том, что словари покрывают все значения,
которые реально может выдать код: незакрытое значение утекает в интерфейс латиницей.
"""

from __future__ import annotations

import pytest

from sue import i18n
from sue.adapter_1c.validator import STAGE_RULES, STAGE_SCHEMA
from sue.db.models import DOC_TYPES, RUN_STATUSES

LOOKUPS = [
    i18n.ru_source,
    i18n.ru_status,
    i18n.ru_stage,
    i18n.ru_severity,
    i18n.ru_doc_type,
    i18n.ru_margin_status,
]


@pytest.mark.parametrize("status", RUN_STATUSES)
def test_every_run_status_is_translated(status: str) -> None:
    assert i18n.ru_status(status) != status


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_every_document_type_is_translated(doc_type: str) -> None:
    assert i18n.ru_doc_type(doc_type) != doc_type


@pytest.mark.parametrize("stage", [STAGE_SCHEMA, STAGE_RULES, "load", "audit"])
def test_every_etl_stage_is_translated(stage: str) -> None:
    assert i18n.ru_stage(stage) != stage


@pytest.mark.parametrize("source", ["accounting", "modeled", "derived"])
def test_every_provenance_source_is_translated(source: str) -> None:
    from sue.domain import provenance

    assert source in provenance.SOURCES
    assert i18n.ru_source(source) != source


def test_mixed_provenance_source_is_translated() -> None:
    assert i18n.ru_source("accounting+modeled") == "учёт+модель"


@pytest.mark.parametrize("lookup", LOOKUPS)
def test_missing_value_renders_as_dash(lookup) -> None:
    """Пустое значение не должно превращаться в «None» на странице."""
    assert lookup(None) == "—"
    assert lookup("") == "—"


@pytest.mark.parametrize("lookup", LOOKUPS)
def test_unknown_value_is_returned_as_is(lookup) -> None:
    """Неизвестное значение показывается как есть — это заметно и легко исправить."""
    assert lookup("совершенно новое значение") == "совершенно новое значение"


def test_every_margin_status_is_translated() -> None:
    from sue.domain.mart import STATUS_BAD, STATUS_OK, STATUS_WARN

    for status in (STATUS_OK, STATUS_WARN, STATUS_BAD):
        assert i18n.ru_margin_status(status) != status


def test_status_and_severity_do_not_contradict_each_other() -> None:
    """«failed» у прогона и «error» у записи журнала — разные сущности, разные подписи."""
    assert i18n.ru_status("failed") != i18n.ru_severity("error")
