"""Регламентный обмен: загрузка пакетов и сверка одной командой.

Загрузка «по кнопке» в интерфейсе не показывает, как обмен работает в эксплуатации:
там его запускает планировщик, а результат нужно проверить без участия человека.
Поэтому задание само сверяет контрольные суммы, сохраняет отчёт файлом и завершается
кодом возврата, по которому планировщик или конвейер отличает успех от сбоя:

* ``0`` — пакеты загружены, контрольные суммы источника и СУЭ совпали;
* ``1`` — хотя бы один пакет отклонён при загрузке;
* ``2`` — пакеты загружены, но сверка обнаружила расхождение;
* ``3`` — обмен не начался: путь не найден или недопустим.

Запуск: ``python -m sue.jobs.exchange --path accounting``.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from sue.adapter_1c import FileExchangeSource, HttpExchangeSource, UnsafeUrlError
from sue.api.paths import UnsafePathError, resolve_import_path
from sue.config import get_settings
from sue.db import prepare_schema, session_scope
from sue.db.models import RUN_STATUS_SUCCESS, EtlRun
from sue.domain.reconciliation import reconcile
from sue.etl.pipeline import EtlPipeline
from sue.logging_config import configure_logging

logger = logging.getLogger("sue.jobs.exchange")

EXIT_OK = 0
EXIT_LOAD_FAILED = 1
EXIT_MISMATCH = 2
EXIT_BAD_PATH = 3

OUTCOME_BY_EXIT = {
    EXIT_OK: "success",
    EXIT_LOAD_FAILED: "load_failed",
    EXIT_MISMATCH: "reconciliation_mismatch",
    EXIT_BAD_PATH: "bad_path",
}


def _run_summary(run: EtlRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "source_file": run.source_file,
        "status": run.status,
        "exchange_id": run.exchange_id,
        "documents_accepted": run.documents_accepted,
        "documents_rejected": run.documents_rejected,
        "lines_accepted": run.lines_accepted,
        "lines_rejected": run.lines_rejected,
        "errors_count": run.errors_count,
        "duration_ms": run.duration_ms,
        "message": run.message,
    }


def run_exchange(
    db: Session,
    label: str,
    *,
    dry_run: bool = False,
    check_reconciliation: bool = True,
) -> tuple[dict[str, Any], int]:
    """Выполнить регламентный обмен и вернуть отчёт вместе с кодом возврата."""
    settings = get_settings()
    started = datetime.now(UTC)

    try:
        path = resolve_import_path(settings.fixtures_dir, label)
    except (UnsafePathError, FileNotFoundError) as exc:
        report = {
            "started_at": started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "source": label,
            "dry_run": dry_run,
            "outcome": OUTCOME_BY_EXIT[EXIT_BAD_PATH],
            "error": str(exc),
            "runs": [],
            "reconciliation": None,
        }
        return report, EXIT_BAD_PATH

    runs = EtlPipeline(db).run_batches(
        FileExchangeSource(path), fallback_label=label, dry_run=dry_run
    )
    failed = [r for r in runs if r.status != RUN_STATUS_SUCCESS]

    # Сверять имеет смысл только то, что записано: в проверочном режиме приёмник не менялся.
    reconciliation: dict[str, Any] | None = None
    if check_reconciliation and not dry_run and not failed:
        reconciliation = reconcile(db, path, label=label)

    exit_code = EXIT_OK
    if failed:
        exit_code = EXIT_LOAD_FAILED
    elif reconciliation is not None and not reconciliation["matched"]:
        exit_code = EXIT_MISMATCH

    finished = datetime.now(UTC)
    report = {
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_ms": int((finished - started).total_seconds() * 1000),
        "source": label,
        "dry_run": dry_run,
        "outcome": OUTCOME_BY_EXIT[exit_code],
        "batches": len(runs),
        "batches_failed": len(failed),
        "documents_accepted": sum(r.documents_accepted for r in runs),
        "lines_accepted": sum(r.lines_accepted for r in runs),
        "errors_count": sum(r.errors_count for r in runs),
        "runs": [_run_summary(r) for r in runs],
        "reconciliation": reconciliation,
    }
    return report, exit_code


def run_exchange_from_url(
    db: Session,
    url: str,
    *,
    scenario: str = "accounting",
    exchange_id: str | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, Any], int]:
    """Загрузить пакеты с эмулятора выгрузки. Сверка с файлами источника здесь не выполняется."""
    started = datetime.now(UTC)
    try:
        source = HttpExchangeSource(url, scenario=scenario, exchange_id=exchange_id)
    except UnsafeUrlError as exc:
        return {
            "started_at": started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "source": url,
            "dry_run": dry_run,
            "outcome": OUTCOME_BY_EXIT[EXIT_BAD_PATH],
            "error": str(exc),
            "runs": [],
            "reconciliation": None,
        }, EXIT_BAD_PATH

    runs = EtlPipeline(db).run_batches(source, fallback_label=url, dry_run=dry_run)
    failed = [r for r in runs if r.status != RUN_STATUS_SUCCESS]
    exit_code = EXIT_LOAD_FAILED if failed else EXIT_OK
    finished = datetime.now(UTC)
    return {
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_ms": int((finished - started).total_seconds() * 1000),
        "source": url,
        "dry_run": dry_run,
        "outcome": OUTCOME_BY_EXIT[exit_code],
        "batches": len(runs),
        "batches_failed": len(failed),
        "documents_accepted": sum(r.documents_accepted for r in runs),
        "lines_accepted": sum(r.lines_accepted for r in runs),
        "errors_count": sum(r.errors_count for r in runs),
        "runs": [_run_summary(r) for r in runs],
        "reconciliation": None,
    }, exit_code


def save_report(report: dict[str, Any], directory: Path | None = None) -> Path:
    """Сохранить отчёт файлом. Имя содержит отметку времени, прежние отчёты сохраняются."""
    target = directory or get_settings().reports_dir
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = target / f"exchange-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m sue.jobs.exchange",
        description="Регламентный обмен: загрузка пакетов из каталога обмена и сверка итогов",
    )
    parser.add_argument(
        "--path",
        default="accounting",
        help="Имя файла или подкаталога внутри каталога обмена (по умолчанию accounting)",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Адрес эмулятора выгрузки (например http://127.0.0.1:8001). Тогда --path не читается",
    )
    parser.add_argument(
        "--scenario",
        default="accounting",
        help="Сценарий эмулятора при загрузке по --url",
    )
    parser.add_argument(
        "--exchange-id",
        default=None,
        help="Один пакет эмулятора; иначе загружается весь сценарий",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только проверить пакеты, ничего не записывая в СУЭ",
    )
    parser.add_argument(
        "--no-reconcile",
        action="store_true",
        help="Не выполнять сверку контрольных сумм после загрузки",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help="Каталог для отчёта (по умолчанию SUE_REPORTS_DIR)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, json_format=settings.log_json)
    try:
        prepare_schema(production=settings.app_env == "production")
    except RuntimeError as exc:
        logger.error("%s", exc)
        return EXIT_BAD_PATH

    with session_scope() as db:
        if args.url:
            report, exit_code = run_exchange_from_url(
                db,
                args.url,
                scenario=args.scenario,
                exchange_id=args.exchange_id,
                dry_run=args.dry_run,
            )
        else:
            report, exit_code = run_exchange(
                db,
                args.path,
                dry_run=args.dry_run,
                check_reconciliation=not args.no_reconcile,
            )

    report_path = save_report(report, args.report_dir)
    log = logger.info if exit_code == EXIT_OK else logger.error
    log(
        "Регламентный обмен завершён: %s",
        report["outcome"],
        extra={
            "source": report["source"],
            "batches": report.get("batches", 0),
            "batches_failed": report.get("batches_failed", 0),
            "exit_code": exit_code,
            "report": str(report_path),
        },
    )
    return exit_code


if __name__ == "__main__":  # pragma: no cover - точка входа
    raise SystemExit(main())
