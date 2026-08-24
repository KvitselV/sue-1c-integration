"""Генератор данных: воспроизводимость, соответствие контракту, манифест."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from sue.adapter_1c import ContractValidator, FileExchangeSource
from sue.config import get_settings
from sue.datagen import SCENARIOS, ScenarioSpec, invalid_examples, totals_of, write_scenario
from sue.datagen.cli import main as datagen_main
from sue.datagen.scenarios import generate

SMALL = ScenarioSpec(
    name="tiny",
    description="Компактный сценарий для тестов генератора",
    seed=101,
    store_refs=("store-01",),
    weeks_override=6,
    docs_per_week=(2, 3),
    split_by_month=False,
)


def test_same_seed_produces_identical_bytes(tmp_path: Path) -> None:
    first = write_scenario(SMALL, tmp_path / "a")
    second = write_scenario(SMALL, tmp_path / "b")
    assert [f["sha256"] for f in first.files] == [f["sha256"] for f in second.files]


def test_different_seed_produces_different_data(tmp_path: Path) -> None:
    other = ScenarioSpec(**{**SMALL.__dict__, "seed": 202})
    first = write_scenario(SMALL, tmp_path / "a")
    second = write_scenario(other, tmp_path / "b")
    assert first.totals["revenue_kopecks"] != second.totals["revenue_kopecks"]


def test_generated_batches_satisfy_contract(tmp_path: Path) -> None:
    write_scenario(SMALL, tmp_path)
    validator = ContractValidator(get_settings().schema_path)

    for batch in FileExchangeSource(tmp_path / "tiny").iter_batches():
        assert validator.validate_schema(batch.payload) == []
        errors = [i for i in validator.validate_rules(batch.payload) if i.severity == "error"]
        assert errors == []


def test_amounts_have_at_most_two_decimals(tmp_path: Path) -> None:
    write_scenario(SMALL, tmp_path)
    for batch in FileExchangeSource(tmp_path / "tiny").iter_batches():
        for document in batch.payload["saleDocuments"]:
            for line in document["lines"]:
                assert round(line["amount"], 2) == line["amount"]
                assert round(line["quantity"], 3) == line["quantity"]


def test_manifest_totals_match_recomputed_totals(tmp_path: Path) -> None:
    generate(tmp_path, ("short_history",))
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["scenarios"][0]

    batches = [b.payload for b in FileExchangeSource(tmp_path / "short_history").iter_batches()]
    assert totals_of(batches) == entry["control_totals"]


def test_manifest_records_provenance_disclaimer(tmp_path: Path) -> None:
    manifest = generate(tmp_path, ("short_history",))
    assert "не являются выгрузкой реальной" in manifest["disclaimer"].lower()
    assert manifest["generator"]["contract_version"] == "2.0"
    assert "overhead_rate" in manifest["modeled_params"]["overhead_params"]


def test_manifest_lists_expectations_for_each_scenario(tmp_path: Path) -> None:
    manifest = generate(tmp_path, ("no_cost", "short_history"))
    expectations = {e["scenario"]: e["expectations"] for e in manifest["scenarios"]}
    assert expectations["no_cost"]["cost_source"] == "modeled"
    assert expectations["short_history"]["weeks"] == 8


def test_return_documents_are_generated_when_requested(tmp_path: Path) -> None:
    spec = ScenarioSpec(
        **{**SMALL.__dict__, "name": "ret", "return_rate": 0.5, "weeks_override": 20}
    )
    write_scenario(spec, tmp_path)
    types = {
        document.get("documentType")
        for batch in FileExchangeSource(tmp_path / "ret").iter_batches()
        for document in batch.payload["saleDocuments"]
    }
    assert "return" in types


def test_piece_goods_have_whole_quantities(tmp_path: Path) -> None:
    """Дробная «упаковка яиц» — признак того, что количество выведено из суммы."""
    from sue.datagen.catalog import PRODUCTS
    from sue.datagen.generator import PIECE_UNITS

    piece_refs = {p.ref for p in PRODUCTS if p.unit in PIECE_UNITS}
    write_scenario(SMALL, tmp_path)

    checked = 0
    for batch in FileExchangeSource(tmp_path / "tiny").iter_batches():
        for document in batch.payload["saleDocuments"]:
            for line in document["lines"]:
                if line["productRef"] in piece_refs:
                    checked += 1
                    assert line["quantity"] == int(line["quantity"]), line
    assert checked, "в наборе не оказалось штучных товаров — проверка бессмысленна"


def test_weight_goods_are_rounded_to_scale_step(tmp_path: Path) -> None:
    from sue.datagen.catalog import PRODUCTS
    from sue.datagen.generator import PIECE_UNITS, WEIGHT_STEP_MILLI

    weight_refs = {p.ref for p in PRODUCTS if p.unit not in PIECE_UNITS}
    write_scenario(SMALL, tmp_path)

    checked = 0
    for batch in FileExchangeSource(tmp_path / "tiny").iter_batches():
        for document in batch.payload["saleDocuments"]:
            for line in document["lines"]:
                if line["productRef"] in weight_refs:
                    checked += 1
                    assert round(line["quantity"] * 1000) % WEIGHT_STEP_MILLI == 0, line
    assert checked


def test_amount_equals_quantity_by_price_minus_discount(tmp_path: Path) -> None:
    """Сумма выводится из количества и цены, а не наоборот."""
    from sue.datagen.catalog import PRODUCTS

    price_by_ref = {p.ref: p.price for p in PRODUCTS}
    spec = ScenarioSpec(**{**SMALL.__dict__, "name": "disc", "discount_share": 1.0})
    write_scenario(spec, tmp_path)

    discounted = 0
    for batch in FileExchangeSource(tmp_path / "disc").iter_batches():
        for document in batch.payload["saleDocuments"]:
            for line in document["lines"]:
                gross = round(price_by_ref[line["productRef"]] * 100) * line["quantity"]
                discount = line.get("discountAmount", 0.0)
                assert abs(round(gross) / 100 - discount - line["amount"]) < 0.01, line
                if discount:
                    discounted += 1
    assert discounted, "скидки не сгенерированы при discount_share=1.0"


def test_closed_weeks_create_gaps_in_history(tmp_path: Path) -> None:
    """Идеальных данных не бывает: часть недель должна остаться без продаж."""
    spec = ScenarioSpec(
        **{
            **SMALL.__dict__,
            "name": "gaps",
            "weeks_override": 60,
            "closed_week_rate": 0.2,
            "split_by_month": False,
        }
    )
    write_scenario(spec, tmp_path)

    dates = sorted(
        date.fromisoformat(document["date"])
        for batch in FileExchangeSource(tmp_path / "gaps").iter_batches()
        for document in batch.payload["saleDocuments"]
    )
    assert dates
    span_weeks = (dates[-1] - dates[0]).days // 7 + 1
    weeks_with_sales = {(d - dates[0]).days // 7 for d in dates}
    assert len(weeks_with_sales) < span_weeks


def test_invalid_examples_cover_documented_violations() -> None:
    examples = invalid_examples()
    expected = {
        "unknown_store_ref",
        "unknown_product_ref",
        "unsupported_contract_version",
        "negative_amount",
        "negative_quantity",
        "zero_quantity_with_amount",
        "excess_precision",
        "duplicate_document_ref",
        "duplicate_line_ref",
        "unexpected_field",
        "invalid_date_format",
        "empty_sale_documents",
        "missing_meta",
        "missing_required_field",
        "date_out_of_period",
        "unknown_document_type",
    }
    assert expected <= set(examples)
    assert all(description for _, description in examples.values())


def test_cli_generates_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert datagen_main(["--out", str(tmp_path), "--scenario", "short_history"]) == 0
    assert (tmp_path / "manifest.json").exists()
    assert "short_history" in capsys.readouterr().out


def test_cli_lists_scenarios(capsys: pytest.CaptureFixture[str]) -> None:
    assert datagen_main(["--list"]) == 0
    output = capsys.readouterr().out
    for name in SCENARIOS:
        assert name in output
