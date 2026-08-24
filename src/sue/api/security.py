"""Контроль доступа к интерфейсу.

Операции записи защищаются общим ключом в заголовке ``X-API-Key``. Ключ по умолчанию
пуст, чтобы локальный запуск не требовал предварительной настройки секретов.
"""

from __future__ import annotations

import hmac
import logging
from typing import Annotated

from fastapi import Depends, Header, status

from sue.api.errors import api_error
from sue.config import Settings, get_settings

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-API-Key"


def _check(settings: Settings, provided: str | None, *, scope: str) -> None:
    if not settings.auth_enabled:
        return
    # Сравнение постоянного времени: обычное == раскрывает ключ по времени ответа.
    # Сравниваются байты: заголовок приходит в latin-1, и на строке с символом вне ASCII
    # сравнение выбросило бы исключение вместо честного отказа в доступе.
    if provided is None or not hmac.compare_digest(
        provided.encode("utf-8", "replace"), settings.api_key.encode("utf-8")
    ):
        logger.warning("Отказ в доступе", extra={"scope": scope, "key_present": bool(provided)})
        raise api_error(
            status.HTTP_401_UNAUTHORIZED,
            "unauthorized",
            f"Требуется корректный заголовок {API_KEY_HEADER}",
            scope=scope,
        )


def require_write_access(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias=API_KEY_HEADER)] = None,
) -> None:
    """Загрузка данных: разрешена только с ключом, если ключ задан."""
    _check(settings, x_api_key, scope="write")


def require_read_access(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias=API_KEY_HEADER)] = None,
) -> None:
    """Чтение: защищается только при включённом режиме ``protect_read``."""
    if not settings.protect_read:
        return
    _check(settings, x_api_key, scope="read")


WriteAccess = Depends(require_write_access)
ReadAccess = Depends(require_read_access)
