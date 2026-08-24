"""CLI генератора данных: ``python -m sue.datagen`` или ``sue-datagen``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sue.config import get_settings
from sue.datagen.scenarios import DEFAULT_SCENARIOS, SCENARIOS, generate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sue-datagen",
        description="Генерация воспроизводимых пакетов обмена формата 1С:Розница",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Каталог вывода (по умолчанию SUE_FIXTURES_DIR)",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        choices=sorted(SCENARIOS),
        help="Сценарий (можно указать несколько раз). По умолчанию — базовый набор",
    )
    parser.add_argument(
        "--with-large",
        action="store_true",
        help="Дополнительно сгенерировать объёмный набор для проверки производительности",
    )
    parser.add_argument(
        "--no-invalid",
        action="store_true",
        help="Не создавать примеры некорректных пакетов",
    )
    parser.add_argument("--list", action="store_true", help="Показать доступные сценарии")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list:
        for name, spec in sorted(SCENARIOS.items()):
            print(f"{name:16} {spec.description}")
        return 0

    names: tuple[str, ...] = tuple(args.scenario) if args.scenario else DEFAULT_SCENARIOS
    if args.with_large and "large" not in names:
        names = (*names, "large")

    out_dir = args.out or get_settings().fixtures_dir
    manifest = generate(out_dir, names, with_invalid=not args.no_invalid)

    print(f"Каталог: {out_dir}")
    for entry in manifest["scenarios"]:
        totals = entry["control_totals"]
        revenue = f"{totals['revenue_kopecks'] / 100:,.2f}".replace(",", " ")
        print(
            f"  {entry['scenario']:14} файлов={len(entry['files']):3} "
            f"документов={totals['documents']:6} строк={totals['lines']:7} "
            f"выручка={revenue} руб."
        )
    if "invalid_examples" in manifest:
        print(f"  invalid        примеров={len(manifest['invalid_examples'])}")
    print(f"Манифест: {(out_dir / 'manifest.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
