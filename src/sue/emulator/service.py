"""HTTP-сервис эмулятора выгрузки 1С:Розница.

Пути повторяют привычный для 1С шаблон HTTP-сервиса ``/hs/exchange``.
Ответ всегда помечает, что это эмулятор, а не живая информационная база.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import Response

from sue import __version__
from sue.adapter_1c import CONTRACT_VERSION
from sue.api.errors import api_error
from sue.config import get_settings
from sue.emulator.catalog import EmulatorCatalog, EmulatorError

router = APIRouter()


def _catalog() -> EmulatorCatalog:
    return EmulatorCatalog(get_settings().fixtures_dir)


@router.get("/hs/exchange/ping")
def ping() -> dict[str, object]:
    return {
        "status": "ok",
        "kind": "emulator",
        "live_1c": False,
        "name": "Эмулятор выгрузки 1С:Розница",
        "contract_version": CONTRACT_VERSION,
        "app_version": __version__,
    }


@router.get("/hs/exchange/info")
def info() -> dict[str, object]:
    catalog = _catalog()
    return {
        "kind": "emulator",
        "live_1c": False,
        "configuration": "1С:Розница 3.0 (эмуляция HTTP-сервиса обмена)",
        "contract_version": CONTRACT_VERSION,
        "comment": (
            "Пакеты синтетические и структурно совместимы с контрактом обмена. "
            "Это не выгрузка действующей информационной базы."
        ),
        "scenarios": catalog.scenarios(),
        "endpoints": {
            "ping": "/hs/exchange/ping",
            "batches": "/hs/exchange/batches",
            "batch": "/hs/exchange/batches/{exchangeId}",
        },
    }


@router.get("/hs/exchange/batches")
def list_batches(
    scenario: str = Query(default="accounting", description="Имя сценария выгрузки"),
) -> dict[str, object]:
    try:
        items = _catalog().list_batches(scenario)
    except EmulatorError as exc:
        raise api_error(404, "emulator_scenario_not_found", str(exc)) from exc
    return {
        "kind": "emulator",
        "live_1c": False,
        "scenario": scenario,
        "count": len(items),
        "items": [
            {
                "exchange_id": item.exchange_id,
                "filename": item.filename,
                "period_from": item.period_from,
                "period_to": item.period_to,
                "documents": item.documents,
                "size_bytes": item.size_bytes,
            }
            for item in items
        ],
    }


@router.get("/hs/exchange/batches/{exchange_id}")
def get_batch(
    exchange_id: str,
    scenario: str = Query(default="accounting"),
) -> Response:
    try:
        raw = _catalog().get_raw(scenario, exchange_id)
    except EmulatorError as exc:
        raise api_error(404, "emulator_batch_not_found", str(exc)) from exc
    return Response(content=raw, media_type="application/json; charset=utf-8")
