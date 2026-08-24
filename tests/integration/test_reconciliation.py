"""Сверка «источник ↔ СУЭ»: допустимое расхождение — ровно ноль."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sue.adapter_1c import Batch, FileExchangeSource
from sue.datagen.generator import totals_of
from sue.domain.reconciliation import batch_totals, db_totals, reconcile, source_totals
from sue.etl.pipeline import EtlPipeline
from tests import factories as f

SCENARIOS = ["main", "short", "nocost"]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_loaded_scenario_matches_source_exactly(db, fixtures_dir: Path, scenario: str) -> None:
    path = fixtures_dir / scenario
    EtlPipeline(db).run_file(path)

    report = reconcile(db, path)
    assert report["diff_minor_units"] == {
        "stores": 0,
        "products": 0,
        "documents": 0,
        "lines": 0,
        "revenue_kopecks": 0,
        "quantity_milli": 0,
        "cost_accounting_kopecks": 0,
    }
    assert report["matched"] is True


def test_manifest_control_totals_match_source_files(fixtures_dir: Path) -> None:
    """Числа в манифесте генератора и пересчёт файлов должны совпадать."""
    batches = [b.payload for b in FileExchangeSource(fixtures_dir / "main").iter_batches()]
    recomputed = totals_of(batches)
    from_files = source_totals(fixtures_dir / "main")

    assert recomputed["revenue_kopecks"] == from_files.revenue_kopecks
    assert recomputed["lines"] == from_files.lines
    assert recomputed["quantity_milli"] == from_files.quantity_milli


def test_repeated_load_does_not_change_totals(db, fixtures_dir: Path) -> None:
    pipeline = EtlPipeline(db)
    pipeline.run_file(fixtures_dir / "main")
    first = db_totals(db)
    pipeline.run_file(fixtures_dir / "main")
    assert db_totals(db) == first


def test_returns_are_netted_in_totals(db) -> None:
    payload = f.batch(
        documents=[
            f.document("d1", lines=[f.line("l1", 100.0, cost=60.0)]),
            f.document("d2", lines=[f.line("l2", 25.0, cost=15.0)], doc_type="return"),
        ]
    )
    EtlPipeline(db).run_batch(Batch("t", payload))

    expected = batch_totals(Batch("t", payload))
    assert expected.revenue_kopecks == 7_500
    assert db_totals(db).revenue_kopecks == expected.revenue_kopecks
    assert db_totals(db).cost_accounting_kopecks == expected.cost_accounting_kopecks


def test_partial_load_is_visible_in_diff(db, tmp_path: Path) -> None:
    """Если часть файлов не загружена, сверка обязана показать расхождение."""
    payload_a = f.batch(exchange_id="EX-A", documents=[f.document("d1")])
    payload_b = f.batch(exchange_id="EX-B", documents=[f.document("d2")])
    (tmp_path / "a.json").write_text(json.dumps(payload_a, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(payload_b, ensure_ascii=False), encoding="utf-8")

    EtlPipeline(db).run_batch(Batch("a", payload_a))
    report = reconcile(db, tmp_path)
    assert report["matched"] is False
    assert report["diff_minor_units"]["documents"] == 1
