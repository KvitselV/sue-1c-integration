"""Точка входа приложения: HTTP-API и веб-интерфейс."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from sue import __version__
from sue.api.errors import register_error_handlers
from sue.api.routes import router, service_router
from sue.config import Settings, get_settings
from sue.db import get_db, prepare_schema
from sue.db.models import EtlError, EtlRun, Store
from sue.domain.analytics import compare_page_payload, dashboard_payload, store_analytics
from sue.domain.exchange_preview import exchange_preview
from sue.domain.mart import compare_periods, default_compare_periods, margin_status
from sue.domain.profitability import list_store_profitability
from sue.i18n import (
    ru_doc_type,
    ru_margin_status,
    ru_severity,
    ru_source,
    ru_stage,
    ru_status,
)
from sue.logging_config import configure_logging
from sue.request_context import request_id_var

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))


def _money(value: Any) -> str:
    """Денежная сумма без копеек с разделителем разрядов."""
    if value is None:
        return "—"
    return f"{Decimal(str(value)):,.0f}".replace(",", "\u202f")


def _money_exact(value: Any) -> str:
    if value is None:
        return "—"
    return f"{Decimal(str(value)):,.2f}".replace(",", "\u202f").replace(".", ",")


def _pct(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}".replace(".", ",")


templates.env.filters["money"] = _money
templates.env.filters["money_exact"] = _money_exact
templates.env.filters["pct"] = _pct
templates.env.filters["ru_source"] = ru_source
templates.env.filters["ru_status"] = ru_status
templates.env.filters["ru_stage"] = ru_stage
templates.env.filters["ru_severity"] = ru_severity
templates.env.filters["ru_doc_type"] = ru_doc_type
templates.env.filters["ru_margin_status"] = ru_margin_status
templates.env.filters["margin_status"] = margin_status
templates.env.globals["app_version"] = __version__


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, json_format=settings.log_json)
    logger.info(
        "Запуск СУЭ",
        extra={
            "version": __version__,
            "env": settings.app_env,
            "database": "sqlite" if settings.is_sqlite else "postgresql",
        },
    )

    prepare_schema(production=settings.app_env == "production")

    yield
    logger.info("Остановка СУЭ")


_settings = get_settings()
_docs_on = _settings.app_env != "production"

app = FastAPI(
    title="СУЭ — интеграция данных формата 1С:Розница",
    description=(
        "Прототип механизма интеграции: контракт файлового обмена, загрузка с аудитом, "
        "рентабельность торговых точек с указанием происхождения каждого показателя "
        "(учёт / модель / расчёт)."
    ),
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if _docs_on else None,
    redoc_url="/redoc" if _docs_on else None,
    openapi_url="/openapi.json" if _docs_on else None,
    license_info={"name": "MIT"},
    openapi_tags=[
        {"name": "служебные", "description": "Версия, живость и готовность сервиса"},
        {"name": "справочники", "description": "Торговые точки, загруженные из источника"},
        {
            "name": "рентабельность",
            "description": "Показатели эффективности с указанием происхождения значений",
        },
        {
            "name": "витрина",
            "description": "Плоские показатели для передачи во внешнюю систему отчётности",
        },
        {"name": "загрузка", "description": "Приём пакетов обмена и журнал загрузок"},
        {"name": "сверка", "description": "Сравнение контрольных сумм источника и СУЭ"},
        {
            "name": "эмулятор 1С",
            "description": "Учебный HTTP-сервис выгрузки по контракту; не живая база 1С",
        },
    ],
)

register_error_handlers(app)
app.include_router(service_router)
app.include_router(router)
if _settings.enable_emulator:
    from sue.emulator.service import router as emulator_router

    app.include_router(
        emulator_router,
        prefix="/emulator/1c",
        tags=["эмулятор 1С"],
    )
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

DbSession = Annotated[Session, Depends(get_db)]
Config = Annotated[Settings, Depends(get_settings)]


@app.middleware("http")
async def request_logging(request: Request, call_next: Any) -> Response:
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        response: Response = await call_next(request)
        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers["x-request-id"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        logger.info(
            "%s %s → %s",
            request.method,
            request.url.path,
            response.status_code,
            extra={
                "request_id": request_id,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
    finally:
        request_id_var.reset(token)


class InvalidPeriodError(ValueError):
    """Период в интерфейсе задан некорректно — показываем HTML-страницу ошибки."""


def _period(
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> tuple[date | None, date | None]:
    if date_from and date_to and date_from > date_to:
        raise InvalidPeriodError("Начало периода позже его конца.")
    return date_from, date_to


Period = Annotated[tuple[date | None, date | None], Depends(_period)]


def _error_page(request: Request, code: int, title: str, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "error.html",
        {"title": title, "active": "", "code": code, "message": message},
        status_code=code,
    )


@app.exception_handler(InvalidPeriodError)
async def _invalid_period_page(request: Request, exc: InvalidPeriodError) -> HTMLResponse:
    return _error_page(request, 422, "Некорректный период", str(exc))


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def page_home(request: Request, db: DbSession, period: Period) -> HTMLResponse:
    payload = dashboard_payload(db, *period)
    return templates.TemplateResponse(
        request,
        "index.html",
        {"title": "Обзор аналитики", "active": "home", **payload},
    )


@app.get("/ui/profitability", response_class=HTMLResponse, include_in_schema=False)
def page_profitability(request: Request, db: DbSession, period: Period) -> HTMLResponse:
    payload = dashboard_payload(db, *period)
    return templates.TemplateResponse(
        request,
        "profitability.html",
        {
            "title": "Рентабельность торговых точек",
            "active": "profit",
            "rows": list_store_profitability(db, *period),
            "charts": payload["charts"],
            "kpis": payload["kpis"],
            "period": payload["period"],
        },
    )


@app.get("/ui/reports", response_class=HTMLResponse, include_in_schema=False)
def page_reports(request: Request, db: DbSession, period: Period) -> HTMLResponse:
    payload = dashboard_payload(db, *period)
    return templates.TemplateResponse(
        request,
        "reports.html",
        {
            "title": "Отчёты",
            "active": "reports",
            "rows": list_store_profitability(db, *period),
            "kpis": payload["kpis"],
            "period": payload["period"],
        },
    )


@app.get("/ui/profitability/{store_id}", response_class=HTMLResponse, include_in_schema=False)
def page_store(request: Request, store_id: int, db: DbSession, period: Period) -> HTMLResponse:
    if db.get(Store, store_id) is None:
        return _error_page(request, 404, "Не найдено", "Торговая точка не найдена.")
    data = store_analytics(db, store_id, *period)
    if data is None:
        return _error_page(
            request,
            404,
            "Нет данных",
            "По этой торговой точке нет загруженных документов за выбранный период.",
        )
    return templates.TemplateResponse(
        request,
        "store.html",
        {"title": data["item"]["store_name"], "active": "profit", **data},
    )


@app.get("/ui/compare", response_class=HTMLResponse, include_in_schema=False)
def page_compare(
    request: Request,
    db: DbSession,
    base_from: Annotated[date | None, Query()] = None,
    base_to: Annotated[date | None, Query()] = None,
    compare_from: Annotated[date | None, Query()] = None,
    compare_to: Annotated[date | None, Query()] = None,
) -> HTMLResponse:
    given = (base_from, base_to, compare_from, compare_to)
    if any(given) and not all(given):
        return _error_page(
            request,
            422,
            "Некорректный период",
            "Укажите все четыре даты сравнения либо оставьте форму пустой — "
            "тогда будут взяты два соседних окна по загруженным данным.",
        )
    if (
        base_from is not None
        and base_to is not None
        and compare_from is not None
        and compare_to is not None
    ):
        periods: tuple[date, date, date, date] | None = (
            base_from,
            base_to,
            compare_from,
            compare_to,
        )
    else:
        periods = default_compare_periods(db)

    if periods is None:
        return _error_page(
            request,
            404,
            "Нет данных",
            "Сравнивать нечего: документы не загружены. Выполните загрузку данных.",
        )

    b_from, b_to, c_from, c_to = periods
    if b_from > b_to or c_from > c_to:
        return _error_page(request, 422, "Некорректный период", "Начало периода позже его конца.")

    period = {
        "base_from": b_from.isoformat(),
        "base_to": b_to.isoformat(),
        "compare_from": c_from.isoformat(),
        "compare_to": c_to.isoformat(),
    }
    rows = compare_periods(db, b_from, b_to, c_from, c_to)
    extras = compare_page_payload(rows, period)
    return templates.TemplateResponse(
        request,
        "compare.html",
        {
            "title": "Сравнение периодов",
            "active": "compare",
            "rows": rows,
            "periods": period,
            **extras,
        },
    )


@app.get("/ui/exchange", response_class=HTMLResponse, include_in_schema=False)
def page_exchange(
    request: Request,
    settings: Config,
    scenario: Annotated[str | None, Query()] = None,
    exchange_id: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    payload = exchange_preview(settings.fixtures_dir, scenario, exchange_id)
    return templates.TemplateResponse(
        request,
        "exchange.html",
        {"title": "Выгрузка 1С", "active": "exchange", **payload},
    )


@app.get("/ui/etl", response_class=HTMLResponse, include_in_schema=False)
def page_etl(request: Request, db: DbSession, settings: Config) -> HTMLResponse:
    runs = db.scalars(select(EtlRun).order_by(EtlRun.id.desc()).limit(50)).all()
    errors: list[EtlError] = []
    if runs:
        errors = list(
            db.scalars(
                select(EtlError)
                .where(EtlError.run_id == runs[0].id)
                .order_by(EtlError.id)
                .limit(20)
            ).all()
        )
    payload = dashboard_payload(db)
    return templates.TemplateResponse(
        request,
        "etl.html",
        {
            "title": "Загрузка данных",
            "active": "etl",
            "runs": runs,
            "latest_errors": errors,
            "charts": payload["charts"],
            "etl_stats": payload["etl_stats"],
            "recon": payload["recon"],
            "auth_enabled": settings.auth_enabled,
            "emulator_enabled": settings.enable_emulator,
        },
    )
