"""Наполнение БД демонстрационными данными.

Порядок: при отсутствии фикстур они генерируются, затем загружаются штатным ETL —
тем же кодом, что и загрузка из интерфейса, без обходных путей.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sue.adapter_1c import FileExchangeSource  # noqa: E402
from sue.config import get_settings  # noqa: E402
from sue.datagen.scenarios import DEFAULT_SCENARIOS, generate  # noqa: E402
from sue.db import create_schema, session_scope  # noqa: E402
from sue.domain.reconciliation import reconcile  # noqa: E402
from sue.etl.pipeline import EtlPipeline  # noqa: E402
from sue.logging_config import configure_logging  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Наполнить БД демонстрационными данными")
    parser.add_argument(
        "--scenario",
        action="append",
        help="Сценарий для загрузки (по умолчанию accounting)",
    )
    parser.add_argument(
        "--regenerate", action="store_true", help="Перегенерировать фикстуры перед загрузкой"
    )
    parser.add_argument(
        "--create-schema",
        action="store_true",
        help="Создать таблицы из моделей (иначе ожидается alembic upgrade head)",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level, json_format=settings.log_json)
    logger = logging.getLogger("seed")

    fixtures_dir = settings.fixtures_dir
    scenarios = tuple(args.scenario) if args.scenario else ("accounting",)

    if args.regenerate or not (fixtures_dir / "manifest.json").exists():
        logger.info("Генерация фикстур: %s", ", ".join(DEFAULT_SCENARIOS))
        generate(fixtures_dir, DEFAULT_SCENARIOS)

    if args.create_schema:
        create_schema()

    exit_code = 0
    with session_scope() as db:
        for name in scenarios:
            path = fixtures_dir / name
            if not path.exists():
                logger.error("Каталог сценария не найден: %s", path)
                exit_code = 1
                continue

            runs = EtlPipeline(db).run_batches(FileExchangeSource(path), fallback_label=name)
            for run in runs:
                print(
                    f"Загрузка #{run.id}: статус={run.status} "
                    f"документов={run.documents_accepted} строк={run.lines_accepted} "
                    f"ошибок={run.errors_count}"
                )
                if run.status == "failed":
                    exit_code = 1

            report = reconcile(db, path)
            print(
                f"Сверка «{name}»: "
                + (
                    "расхождений нет"
                    if report["matched"]
                    else f"РАСХОЖДЕНИЕ {report['diff_minor_units']}"
                )
            )
            if not report["matched"]:
                exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
