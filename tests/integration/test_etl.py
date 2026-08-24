"""ETL: идемпотентность, аудит, обработка ошибок, возвраты."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from sue.adapter_1c import Batch, UploadedFileSource
from sue.db.models import (
    RUN_STATUS_FAILED,
    RUN_STATUS_SUCCESS,
    EtlError,
    EtlRun,
    Product,
    SaleDocument,
    SaleLine,
    Store,
)
from sue.etl.pipeline import EtlPipeline
from tests import factories as f


def _counts(db) -> tuple[int, int, int, int]:
    return (
        db.scalar(select(func.count(Store.id))),
        db.scalar(select(func.count(Product.id))),
        db.scalar(select(func.count(SaleDocument.id))),
        db.scalar(select(func.count(SaleLine.id))),
    )


def test_batch_is_loaded_with_success_status(db) -> None:
    run = EtlPipeline(db).run_batch(Batch("t", f.batch()))
    assert run.status == RUN_STATUS_SUCCESS
    assert run.documents_accepted == 1
    assert run.lines_accepted == 1
    assert run.errors_count == 0
    assert run.duration_ms is not None
    assert run.exchange_id == "EX-TEST-001"
    assert run.contract_version == "2.0"


def test_batch_with_one_bad_document_is_rejected_entirely(db) -> None:
    """Пакет принимается целиком или не принимается вовсе.

    Частичная загрузка сделала бы сверку контрольных сумм источника и приёмника
    заведомо расходящейся, поэтому нарушение в одном документе отклоняет весь пакет,
    а корректные документы этого пакета не загружаются.
    """
    payload = f.batch(
        documents=[
            f.document("good", lines=[f.line("l1", 100.0)]),
            f.document("bad", store_ref="UNKNOWN", lines=[f.line("l2", 200.0)]),
        ]
    )
    run = EtlPipeline(db).run_batch(Batch("t", payload))

    assert run.status == RUN_STATUS_FAILED
    assert _counts(db) == (0, 0, 0, 0)
    assert run.errors_count == 1
    error = run.errors[0]
    assert error.stage == "validate_rules"
    assert error.source_ref == "bad"
    assert error.location == "saleDocuments/1/storeRef"


def test_audit_records_name_inside_exchange_directory(db, fixtures_dir: Path) -> None:
    """В журнале должно быть имя внутри каталога обмена, а не путь машины.

    Абсолютный путь делает одну и ту же загрузку по-разному выглядящей на разных
    машинах и раскрывает устройство сервера в отчёте о загрузках.
    """
    run = EtlPipeline(db).run_file(fixtures_dir / "main")
    assert run.source_file.startswith("main/")
    assert ":" not in run.source_file and "\\" not in run.source_file


def test_repeated_load_is_idempotent(db, fixtures_dir: Path) -> None:
    pipeline = EtlPipeline(db)
    pipeline.run_file(fixtures_dir / "main")
    first = _counts(db)

    pipeline.run_file(fixtures_dir / "main")
    assert _counts(db) == first


def test_reload_replaces_lines_instead_of_duplicating(db) -> None:
    pipeline = EtlPipeline(db)
    pipeline.run_batch(Batch("t", f.batch(documents=[f.document(lines=[f.line("l1", 100.0)])])))
    pipeline.run_batch(
        Batch(
            "t",
            f.batch(
                documents=[
                    f.document(lines=[f.line("l1", 150.0), f.line("l2", 50.0)]),
                ]
            ),
        )
    )
    lines = db.scalars(select(SaleLine).order_by(SaleLine.source_ref)).all()
    assert [line.revenue_kopecks for line in lines] == [15_000, 5_000]


def test_line_moved_between_documents_is_not_duplicated(db) -> None:
    pipeline = EtlPipeline(db)
    pipeline.run_batch(
        Batch("t", f.batch(documents=[f.document("d1", lines=[f.line("l1", 100.0)])]))
    )
    pipeline.run_batch(
        Batch("t", f.batch(documents=[f.document("d2", lines=[f.line("l1", 100.0)])]))
    )

    lines = db.scalars(select(SaleLine)).all()
    assert len(lines) == 1
    assert lines[0].document.source_ref == "d2"


def test_updated_catalog_fields_are_applied(db) -> None:
    pipeline = EtlPipeline(db)
    pipeline.run_batch(Batch("t", f.batch()))
    pipeline.run_batch(Batch("t", f.batch(stores=[f.store(name="ТТ Переименованная")])))
    store = db.scalars(select(Store)).one()
    assert store.name == "ТТ Переименованная"


def test_invalid_batch_is_rejected_and_audited(db) -> None:
    payload = f.batch(documents=[f.document(store_ref="unknown")])
    run = EtlPipeline(db).run_batch(Batch("bad", payload))

    assert run.status == RUN_STATUS_FAILED
    assert run.errors_count > 0
    assert run.lines_accepted == 0
    errors = db.scalars(select(EtlError).where(EtlError.run_id == run.id)).all()
    assert errors and all(e.location for e in errors)
    assert _counts(db) == (0, 0, 0, 0)


def test_audit_record_survives_failed_load(db) -> None:
    """Даже при отказе на этапе записи история попытки остаётся в журнале."""
    pipeline = EtlPipeline(db)
    payload = f.batch(documents=[f.document(date="2026-13-45")])
    run = pipeline.run_batch(Batch("bad-date", payload))

    assert run.status == RUN_STATUS_FAILED
    assert db.get(EtlRun, run.id) is not None


def test_dry_run_validates_without_writing(db) -> None:
    run = EtlPipeline(db).run_batch(Batch("t", f.batch()), dry_run=True)
    assert run.status == RUN_STATUS_SUCCESS
    assert run.dry_run is True
    assert run.documents_accepted == 1
    assert _counts(db) == (0, 0, 0, 0)


def test_returns_are_stored_with_negative_sign(db) -> None:
    documents = [
        f.document("d1", lines=[f.line("l1", 100.0)]),
        f.document("d2", lines=[f.line("l2", 30.0)], doc_type="return"),
    ]
    run = EtlPipeline(db).run_batch(Batch("t", f.batch(documents=documents)))

    assert run.revenue_kopecks == 7_000
    total = db.scalar(select(func.sum(SaleLine.revenue_kopecks)))
    assert total == 7_000


def test_counters_are_reported_separately(db, fixtures_dir: Path) -> None:
    runs = EtlPipeline(db).run_batches(
        UploadedFileSource(
            "short.json", (fixtures_dir / "short" / "exchange_short_001.json").read_bytes()
        )
    )
    run = runs[-1]
    assert run.stores_upserted == 1
    assert run.products_upserted == 10
    assert run.documents_accepted > 0
    assert run.lines_accepted > run.documents_accepted
    assert run.records_accepted == (
        run.stores_upserted + run.products_upserted + run.documents_accepted + run.lines_accepted
    )


def test_empty_directory_produces_failed_run(db, tmp_path: Path) -> None:
    run = EtlPipeline(db).run_file(tmp_path)
    assert run.status == RUN_STATUS_FAILED
    assert "Нет JSON" in (run.message or "")


def test_missing_path_produces_failed_run(db, tmp_path: Path) -> None:
    run = EtlPipeline(db).run_file(tmp_path / "absent")
    assert run.status == RUN_STATUS_FAILED


def test_broken_json_produces_failed_run(db, tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    run = EtlPipeline(db).run_file(tmp_path)
    assert run.status == RUN_STATUS_FAILED
    assert "JSON" in (run.message or "")


def test_oversized_file_is_rejected(db, tmp_path: Path) -> None:
    from sue.adapter_1c import FileExchangeSource, SourceError

    (tmp_path / "big.json").write_text("{}" + " " * 100, encoding="utf-8")
    with pytest.raises(SourceError, match="превышает лимит"):
        list(FileExchangeSource(tmp_path, max_bytes=10).iter_batches())


def test_all_invalid_examples_are_rejected(db, fixtures_dir: Path) -> None:
    from sue.adapter_1c import FileExchangeSource

    runs = EtlPipeline(db).run_batches(FileExchangeSource(fixtures_dir / "invalid"))
    assert runs
    assert all(run.status == RUN_STATUS_FAILED for run in runs)
    assert _counts(db) == (0, 0, 0, 0)


def test_every_document_links_to_its_run(db) -> None:
    run = EtlPipeline(db).run_batch(Batch("t", f.batch()))
    document = db.scalars(select(SaleDocument)).one()
    assert document.etl_run_id == run.id
