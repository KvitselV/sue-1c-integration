"""Абстракция источника данных формата 1С.

Реализован файловый обмен (:class:`~sue.adapter_1c.file_source.FileExchangeSource`).
Интерфейс сохранён, чтобы подключение к живой информационной базе через OData
добавлялось без изменения ETL и доменного слоя.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

CONTRACT_VERSION = "2.0"
SUPPORTED_CONTRACT_VERSIONS = frozenset({"2.0"})


class SourceError(Exception):
    """Данные источника невозможно прочитать (файл, формат, размер)."""


@dataclass(frozen=True)
class Batch:
    """Один пакет обмена вместе с метаданными происхождения."""

    label: str
    payload: dict[str, Any]
    content_hash: str = ""
    size_bytes: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.meta and isinstance(self.payload.get("meta"), dict):
            object.__setattr__(self, "meta", self.payload["meta"])

    @classmethod
    def from_bytes(cls, label: str, raw: bytes) -> Batch:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise SourceError(f"{label}: файл не в кодировке UTF-8") from exc
        except json.JSONDecodeError as exc:
            raise SourceError(
                f"{label}: некорректный JSON ({exc.msg}, строка {exc.lineno})"
            ) from exc
        if not isinstance(payload, dict):
            raise SourceError(f"{label}: корень документа должен быть объектом JSON")
        return cls(
            label=label,
            payload=payload,
            content_hash=hashlib.sha256(raw).hexdigest(),
            size_bytes=len(raw),
            meta=payload.get("meta", {}) if isinstance(payload.get("meta"), dict) else {},
        )

    @property
    def contract_version(self) -> str | None:
        value = self.meta.get("contractVersion")
        return str(value) if value is not None else None

    @property
    def exchange_id(self) -> str | None:
        value = self.meta.get("exchangeId")
        return str(value) if value is not None else None


class OneCSource(ABC):
    """Точка расширения источника данных формата 1С."""

    @abstractmethod
    def iter_batches(self) -> Iterator[Batch]:
        """Последовательно отдать пакеты обмена."""

    def load_batches(self) -> list[Batch]:
        return list(self.iter_batches())


class ODataSource(OneCSource):
    """Заглушка под живое подключение к 1С через REST/OData.

    Реализация требует доступа к информационной базе 1С. Наличие интерфейса позволяет
    добавить онлайн-обмен без изменения ETL и расчёта показателей.
    """

    def __init__(self, endpoint: str, user: str, password: str) -> None:
        self.endpoint = endpoint
        self.user = user
        self.password = password

    def iter_batches(self) -> Iterator[Batch]:
        raise NotImplementedError(
            "Источник OData не реализован: приложение использует файловый обмен. "
            "Интерфейс сохранён для расширения при появлении доступа к базе 1С."
        )
