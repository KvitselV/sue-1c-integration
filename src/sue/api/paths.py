"""Безопасное разрешение путей импорта.

Приём произвольного пути от клиента — путь к чтению любого файла на сервере.
Поэтому пользователь задаёт только относительное имя внутри каталога фикстур,
а результат разрешения проверяется на принадлежность этому каталогу уже после
раскрытия символических ссылок.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_SUBDIR = "accounting"


class UnsafePathError(ValueError):
    """Путь выходит за пределы разрешённого каталога."""


def resolve_import_path(base_dir: Path, relative: str | None) -> Path:
    base = base_dir.resolve()
    candidate_raw = relative or DEFAULT_SUBDIR

    candidate = Path(candidate_raw)
    if candidate.is_absolute() or candidate.drive or candidate.root:
        raise UnsafePathError("Абсолютные пути запрещены; укажите имя внутри каталога фикстур")
    if any(part == ".." for part in candidate.parts):
        raise UnsafePathError("Переход в родительский каталог запрещён")

    resolved = (base / candidate).resolve()
    if resolved != base and base not in resolved.parents:
        raise UnsafePathError(f"Путь вне каталога фикстур: {candidate_raw}")
    if not resolved.exists():
        raise FileNotFoundError(candidate_raw)
    return resolved
