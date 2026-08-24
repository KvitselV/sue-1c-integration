"""Регламентный обмен: отчёт, сверка и коды возврата для планировщика."""

from __future__ import annotations

import json
from pathlib import Path

from sue.db.models import EtlRun
from sue.jobs.exchange import (
    EXIT_BAD_PATH,
    EXIT_LOAD_FAILED,
    EXIT_OK,
    main,
    run_exchange,
    run_exchange_from_url,
    save_report,
)


def test_successful_exchange_loads_and_reconciles(db, fixtures_dir: Path) -> None:
    report, exit_code = run_exchange(db, "main")

    assert exit_code == EXIT_OK
    assert report["outcome"] == "success"
    assert report["batches"] >= 1
    assert report["batches_failed"] == 0
    assert report["documents_accepted"] > 0
    assert report["reconciliation"]["matched"] is True
    assert report["reconciliation"]["source"] == "main"


def test_report_contains_no_absolute_paths(db, fixtures_dir: Path) -> None:
    """Отчёт уходит в журнал планировщика: устройство сервера в нём раскрывать не нужно."""
    report, _ = run_exchange(db, "main")

    text = json.dumps(report, ensure_ascii=False)
    assert ":\\" not in text
    assert str(fixtures_dir) not in text


def test_dry_run_changes_nothing_and_skips_reconciliation(db, fixtures_dir: Path) -> None:
    """Сверять проверочный прогон нечем: приёмник по определению не изменялся."""
    report, exit_code = run_exchange(db, "main", dry_run=True)

    assert exit_code == EXIT_OK
    assert report["dry_run"] is True
    assert report["reconciliation"] is None
    assert db.query(EtlRun).count() > 0
    assert all(run.dry_run for run in db.query(EtlRun).all())


def test_rejected_package_yields_failure_exit_code(db, fixtures_dir: Path) -> None:
    """Планировщик должен узнать о сбое по коду возврата, а не по чтению журнала."""
    invalid = next((fixtures_dir / "invalid").glob("*.json"))
    report, exit_code = run_exchange(db, f"invalid/{invalid.name}")

    assert exit_code == EXIT_LOAD_FAILED
    assert report["outcome"] == "load_failed"
    assert report["batches_failed"] == 1
    assert report["reconciliation"] is None


def test_unknown_path_does_not_start_the_exchange(db, fixtures_dir: Path) -> None:
    report, exit_code = run_exchange(db, "нет-такого-каталога")

    assert exit_code == EXIT_BAD_PATH
    assert report["outcome"] == "bad_path"
    assert report["runs"] == []
    assert db.query(EtlRun).count() == 0


def test_foreign_emulator_url_does_not_start_the_exchange(db) -> None:
    report, exit_code = run_exchange_from_url(db, "http://example.com/hs/exchange")

    assert exit_code == EXIT_BAD_PATH
    assert report["outcome"] == "bad_path"
    assert report["runs"] == []
    assert db.query(EtlRun).count() == 0


def test_path_outside_exchange_directory_is_refused(db, fixtures_dir: Path) -> None:
    report, exit_code = run_exchange(db, "../secrets.json")

    assert exit_code == EXIT_BAD_PATH
    assert "родительский каталог" in report["error"]


def test_command_line_run_reports_success_and_saves_file(
    db, fixtures_dir: Path, tmp_path: Path
) -> None:
    """Именно эту команду ставят в планировщик, поэтому она проверяется целиком."""
    exit_code = main(["--path", "main", "--report-dir", str(tmp_path)])

    assert exit_code == EXIT_OK
    reports = list(tmp_path.glob("exchange-*.json"))
    assert len(reports) == 1
    assert json.loads(reports[0].read_text(encoding="utf-8"))["reconciliation"]["matched"] is True


def test_command_line_run_signals_failure_by_exit_code(
    db, fixtures_dir: Path, tmp_path: Path
) -> None:
    invalid = next((fixtures_dir / "invalid").glob("*.json"))
    exit_code = main(
        ["--path", f"invalid/{invalid.name}", "--report-dir", str(tmp_path), "--no-reconcile"]
    )

    assert exit_code == EXIT_LOAD_FAILED


def test_report_is_saved_as_readable_json(db, fixtures_dir: Path, tmp_path: Path) -> None:
    report, _ = run_exchange(db, "main")
    path = save_report(report, tmp_path)

    assert path.parent == tmp_path
    assert path.name.startswith("exchange-")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["outcome"] == "success"
    # Русские подписи должны читаться в файле как текст, а не как escape-последовательности.
    assert "\\u0" not in path.read_text(encoding="utf-8")
