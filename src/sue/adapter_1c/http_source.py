"""Источник пакетов обмена по HTTP — подключение к эмулятору выгрузки 1С.

Живая информационная база не используется. Клиент забирает JSON по контракту 2.0
с HTTP-сервиса вида ``/hs/exchange`` (как публикуют обработки 1С).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

from sue.adapter_1c.base import Batch, OneCSource, SourceError
from sue.config import get_settings

logger = logging.getLogger(__name__)

FetchFn = Callable[[str], bytes]


class UnsafeUrlError(ValueError):
    """Адрес источника не разрешён (схема, хост или формат)."""


def resolve_export_url(raw: str, allowed_hosts: tuple[str, ...] | None = None) -> str:
    """Проверить адрес эмулятора: только http(s) и хосты из белого списка."""
    text = raw.strip()
    if not text:
        raise UnsafeUrlError("Адрес эмулятора пуст")
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeUrlError("Допускаются только адреса http и https")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("Учётные данные в адресе не принимаются")
    host = (parsed.hostname or "").lower()
    allowed = allowed_hosts if allowed_hosts is not None else get_settings().emulator_host_list
    if host not in allowed:
        raise UnsafeUrlError("Хост эмулятора не в списке разрешённых: " + ", ".join(allowed))
    return text.rstrip("/")


def _http_get(url: str, timeout: float, max_bytes: int) -> bytes:
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            data = bytes(response.read(max_bytes + 1))
    except HTTPError as exc:
        raise SourceError(f"Эмулятор вернул HTTP {exc.code} для {url}") from exc
    except URLError as exc:
        raise SourceError(f"Не удалось подключиться к эмулятору: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SourceError("Превышено время ожидания ответа эмулятора") from exc
    if len(data) > max_bytes:
        raise SourceError(f"Ответ эмулятора превышает лимит {max_bytes} байт")
    return data


class HttpExchangeSource(OneCSource):
    """Чтение пакетов с HTTP-сервиса выгрузки ``/hs/exchange``."""

    def __init__(
        self,
        base_url: str,
        *,
        scenario: str = "accounting",
        exchange_id: str | None = None,
        fetch: FetchFn | None = None,
        timeout: float = 10.0,
    ) -> None:
        settings = get_settings()
        self.base_url = resolve_export_url(base_url, settings.emulator_host_list)
        self.scenario = scenario
        self.exchange_id = exchange_id
        self.timeout = timeout
        self.max_bytes = settings.max_upload_bytes
        self._fetch = fetch or (lambda url: _http_get(url, self.timeout, self.max_bytes))

    def ping(self) -> dict[str, object]:
        payload = json.loads(self._fetch(self._href("/hs/exchange/ping")).decode("utf-8"))
        if not isinstance(payload, dict):
            raise SourceError("Эмулятор вернул некорректный ответ ping")
        return payload

    def iter_batches(self) -> Iterator[Batch]:
        self.ping()
        if self.exchange_id:
            yield self._download(self.exchange_id)
            return
        listing = json.loads(
            self._fetch(self._href(f"/hs/exchange/batches?scenario={quote(self.scenario)}")).decode(
                "utf-8"
            )
        )
        items = listing.get("items") if isinstance(listing, dict) else None
        if not isinstance(items, list) or not items:
            raise SourceError(f"Эмулятор не вернул пакеты сценария {self.scenario}")
        for item in items:
            if not isinstance(item, dict) or not item.get("exchange_id"):
                continue
            yield self._download(str(item["exchange_id"]))

    def _download(self, exchange_id: str) -> Batch:
        path = f"/hs/exchange/batches/{quote(exchange_id, safe='')}?scenario={quote(self.scenario)}"
        raw = self._fetch(self._href(path))
        logger.info(
            "Получен пакет с эмулятора выгрузки",
            extra={"exchange_id": exchange_id, "size_bytes": len(raw), "url": self.base_url},
        )
        return Batch.from_bytes(f"emulator:{self.scenario}/{exchange_id}", raw)

    def _href(self, path: str) -> str:
        return urljoin(self.base_url + "/", path.lstrip("/"))
