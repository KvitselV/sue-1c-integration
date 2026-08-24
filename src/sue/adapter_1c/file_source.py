"""Файловый обмен: чтение пакетов из каталога, файла или байтов загрузки."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from sue.adapter_1c.base import Batch, OneCSource, SourceError
from sue.config import get_settings

logger = logging.getLogger(__name__)


def batch_label(file: Path) -> str:
    """Метка пакета для журнала загрузок.

    Записывается имя внутри каталога обмена, а не путь файловой системы: абсолютный
    путь меняется от машины к машине, из-за чего один и тот же обмен выглядит
    в журнале по-разному, и раскрывает устройство сервера получателю отчёта.
    """
    try:
        return file.resolve().relative_to(get_settings().fixtures_dir.resolve()).as_posix()
    except (ValueError, OSError):
        return file.name


class FileExchangeSource(OneCSource):
    """Загрузка JSON-выгрузок по контракту 1С:Розница из файла или каталога."""

    def __init__(self, path: Path | str, max_bytes: int | None = None) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes if max_bytes is not None else get_settings().max_upload_bytes

    def files(self) -> list[Path]:
        if self.path.is_file():
            return [self.path]
        if self.path.is_dir():
            return sorted(p for p in self.path.glob("*.json") if p.is_file())
        raise SourceError(f"Путь не найден: {self.path}")

    def iter_batches(self) -> Iterator[Batch]:
        for file in self.files():
            size = file.stat().st_size
            if size > self.max_bytes:
                raise SourceError(
                    f"{file.name}: размер {size} байт превышает лимит {self.max_bytes} байт"
                )
            logger.info("Чтение пакета обмена", extra={"file": str(file), "size_bytes": size})
            yield Batch.from_bytes(batch_label(file), file.read_bytes())


class UploadedFileSource(OneCSource):
    """Пакет, полученный по HTTP (multipart) — без записи на диск."""

    def __init__(self, filename: str, content: bytes, max_bytes: int | None = None) -> None:
        limit = max_bytes if max_bytes is not None else get_settings().max_upload_bytes
        if len(content) > limit:
            raise SourceError(
                f"{filename}: размер {len(content)} байт превышает лимит {limit} байт"
            )
        self.filename = filename
        self.content = content

    def iter_batches(self) -> Iterator[Batch]:
        yield Batch.from_bytes(f"upload:{self.filename}", self.content)
