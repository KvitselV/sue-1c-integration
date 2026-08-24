"""Единая настройка логирования (текстовый или JSON-формат)."""

from __future__ import annotations

import contextlib
import json
import logging
import sys
from typing import Any

_CONFIGURED = False

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _console_stream() -> Any:
    """Поток вывода, устойчивый к однобайтовой кодировке консоли.

    В консоли Windows кодировка по умолчанию — cp1251, и первая же запись со стрелкой
    или другим символом вне неё превращается в трассировку «Logging error» вместо строки
    журнала. Замена непредставимых символов оставляет журнал читаемым.
    """
    stream = sys.stdout
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        # Поток может не поддерживать перенастройку (например, подменён в тестах).
        with contextlib.suppress(ValueError, OSError):
            reconfigure(errors="backslashreplace")
    return stream


def configure_logging(level: str = "INFO", *, json_format: bool = False) -> None:
    global _CONFIGURED
    handler = logging.StreamHandler(_console_stream())
    handler.setFormatter(
        JsonFormatter()
        if json_format
        else logging.Formatter("%(asctime)s %(levelname)-8s %(name)s | %(message)s")
    )

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn создаёт собственные обработчики — переиспользуем корневой
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    _CONFIGURED = True


def is_configured() -> bool:
    return _CONFIGURED
