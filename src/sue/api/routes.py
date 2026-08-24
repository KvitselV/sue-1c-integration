"""HTTP-API СУЭ."""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Sequence
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from sue import __version__
from sue.adapter_1c import (
    CONTRACT_VERSION,
    HttpExchangeSource,
    SourceError,
    UnsafeUrlError,
    UploadedFileSource,
)
from sue.api.errors import api_error
from sue.api.paths import UnsafePathError, resolve_import_path
from sue.api.schemas import (
    CategoryRowOut,
    CompareRowOut,
    EtlErrorOut,
    EtlRunOut,
    HealthOut,
    ImportRequest,
    MartRowOut,
    Page,
    ProfitabilityOut,
    PullRequest,
    ReconciliationOut,
    StoreOut,
    VersionOut,
)
from sue.api.security import ReadAccess, WriteAccess
from sue.config import Settings, get_settings
from sue.db import get_db
from sue.db.models import EtlError, EtlRun, Store
from sue.domain.mart import MART_COLUMNS, compare_periods, kpi_mart
from sue.domain.profitability import (
    category_breakdown,
    compute_store_profitability,
    list_store_profitability,
)
from sue.domain.reconciliation import reconcile
from sue.etl.pipeline import EtlPipeline
from sue.reports.pdf import render_network_report, render_store_report

logger = logging.getLogger(__name__)

# Живость, готовность и версия остаются открытыми даже при включённой защите чтения:
# пробы оркестратора и проверка развёртывания не должны знать секретов.
service_router = APIRouter(prefix="/api")
router = APIRouter(prefix="/api", dependencies=[ReadAccess])

DbSession = Annotated[Session, Depends(get_db)]
Config = Annotated[Settings, Depends(get_settings)]


def _period(
    date_from: Annotated[date | None, Query(description="Начало периода включительно")] = None,
    date_to: Annotated[date | None, Query(description="Конец периода включительно")] = None,
) -> tuple[date | None, date | None]:
    if date_from and date_to and date_from > date_to:
        raise api_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_period",
            "Начало периода позже его конца",
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
        )
    return date_from, date_to


Period = Annotated[tuple[date | None, date | None], Depends(_period)]


def _paging(
    settings: Config,
    limit: Annotated[int | None, Query(ge=1, description="Размер страницы")] = None,
    offset: Annotated[int, Query(ge=0, description="Смещение от начала выборки")] = 0,
) -> tuple[int, int]:
    size = settings.api_page_size if limit is None else limit
    if size > settings.api_max_page_size:
        raise api_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_paging",
            f"Размер страницы не может превышать {settings.api_max_page_size}",
            limit=size,
        )
    return size, offset


Paging = Annotated[tuple[int, int], Depends(_paging)]


# --- служебные ----------------------------------------------------------------


@service_router.get("/health", response_model=HealthOut, tags=["служебные"])
def health() -> HealthOut:
    """Живость процесса. Не обращается к БД — пригодно для liveness-пробы."""
    return HealthOut(status="ok", checks={"process": "ok"})


@service_router.get("/health/live", response_model=HealthOut, tags=["служебные"])
def health_live() -> HealthOut:
    return HealthOut(status="ok", checks={"process": "ok"})


@service_router.get("/health/ready", response_model=HealthOut, tags=["служебные"])
def health_ready(db: DbSession, response: Response) -> HealthOut:
    """Готовность обслуживать запросы: проверяется доступность БД."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning("БД недоступна: %s", exc)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthOut(status="degraded", checks={"database": "unavailable"})
    return HealthOut(status="ok", checks={"database": "ok"})


@service_router.get("/version", response_model=VersionOut, tags=["служебные"])
def version(settings: Config) -> VersionOut:
    return VersionOut(
        name="sue",
        version=__version__,
        contract_version=CONTRACT_VERSION,
        app_env=settings.app_env,
        database="sqlite" if settings.is_sqlite else "postgresql",
    )


# --- справочники --------------------------------------------------------------


@router.get("/stores", response_model=Page[StoreOut], tags=["справочники"])
def stores(db: DbSession, paging: Paging) -> Page[StoreOut]:
    limit, offset = paging
    total = db.scalar(select(func.count(Store.id))) or 0
    rows = db.scalars(select(Store).order_by(Store.code).limit(limit).offset(offset)).all()
    return Page[StoreOut](
        items=[StoreOut.model_validate(s) for s in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# --- рентабельность -----------------------------------------------------------


@router.get("/profitability", response_model=list[ProfitabilityOut], tags=["рентабельность"])
def profitability(db: DbSession, period: Period) -> list[dict[str, Any]]:
    return list_store_profitability(db, *period)


def _csv_response(
    rows: list[dict[str, Any]],
    columns: Sequence[tuple[str, str]],
    filename: str,
) -> Response:
    """Выгрузка в CSV: разделитель «;» и BOM — иначе Excel открывает файл одной колонкой."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow([title for _, title in columns])
    for row in rows:
        line = []
        for key, _ in columns:
            value = row[key]
            line.append(value["value"] if isinstance(value, dict) else value)
        writer.writerow(line)

    return Response(
        content=buffer.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/profitability.csv", tags=["рентабельность"])
def profitability_csv(db: DbSession, period: Period) -> Response:
    """Выгрузка показателей в CSV (разделитель «;», BOM — для корректного открытия в Excel)."""
    columns = [
        ("store_code", "Код ТТ"),
        ("store_name", "Торговая точка"),
        ("city", "Город"),
        ("period_from", "Период с"),
        ("period_to", "Период по"),
        ("revenue", "Выручка"),
        ("returns", "Возвраты"),
        ("cost", "Себестоимость"),
        ("gross_profit", "Валовая прибыль"),
        ("gross_margin_pct", "Валовая маржа, %"),
        ("overhead", "Накладные"),
        ("operating_profit", "Операционная прибыль"),
        ("operating_margin_pct", "Операционная маржа, %"),
        ("cost_modeled_share_pct", "Доля моделируемой себестоимости, %"),
    ]
    return _csv_response(list_store_profitability(db, *period), columns, "profitability.csv")


def _pdf_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/profitability/report.pdf", tags=["рентабельность"])
def profitability_network_pdf(db: DbSession, period: Period) -> Response:
    """Сводный PDF по всем торговым точкам за период."""
    rows = list_store_profitability(db, *period)
    if not rows:
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "no_data",
            "Нет данных по торговым точкам за указанный период",
        )
    date_from, date_to = period
    return _pdf_response(
        render_network_report(
            rows,
            period_from=date_from.isoformat() if date_from else None,
            period_to=date_to.isoformat() if date_to else None,
        ),
        "sue-stores.pdf",
    )


@router.get(
    "/profitability/compare",
    response_model=list[CompareRowOut],
    tags=["рентабельность"],
)
def profitability_compare(
    db: DbSession,
    base_from: Annotated[date, Query(description="Начало базового периода")],
    base_to: Annotated[date, Query(description="Конец базового периода")],
    compare_from: Annotated[date, Query(description="Начало сравниваемого периода")],
    compare_to: Annotated[date, Query(description="Конец сравниваемого периода")],
) -> list[dict[str, Any]]:
    """Сравнить показатели точек за два периода.

    Маршрут объявлен до ``/profitability/{store_id}``: иначе слово ``compare`` попало бы
    в параметр-идентификатор и запрос отклонялся бы как некорректный.
    """
    for label, start, end in (
        ("base", base_from, base_to),
        ("compare", compare_from, compare_to),
    ):
        if start > end:
            raise api_error(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "invalid_period",
                "Начало периода позже его конца",
                period=label,
                date_from=start.isoformat(),
                date_to=end.isoformat(),
            )
    return compare_periods(db, base_from, base_to, compare_from, compare_to)


@router.get("/profitability/{store_id}", response_model=ProfitabilityOut, tags=["рентабельность"])
def profitability_store(
    store_id: int,
    db: DbSession,
    period: Period,
    sensitivity_delta: Annotated[
        float,
        Query(
            ge=-0.5,
            le=0.5,
            description="Сдвиг ставки накладных расходов для анализа чувствительности",
        ),
    ] = 0.0,
) -> dict[str, Any]:
    item = compute_store_profitability(db, store_id, *period, sensitivity_delta=sensitivity_delta)
    if item is None:
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "no_data",
            "Нет данных по торговой точке за указанный период",
            store_id=store_id,
        )
    return item.to_dict()


@router.get("/profitability/{store_id}/report.pdf", tags=["рентабельность"])
def profitability_store_pdf(store_id: int, db: DbSession, period: Period) -> Response:
    """PDF-отчёт по одной торговой точке."""
    if db.get(Store, store_id) is None:
        raise api_error(
            status.HTTP_404_NOT_FOUND, "not_found", "Торговая точка не найдена", store_id=store_id
        )
    item = compute_store_profitability(db, store_id, *period)
    if item is None:
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "no_data",
            "Нет данных по торговой точке за указанный период",
            store_id=store_id,
        )
    categories = category_breakdown(db, store_id, *period)
    payload = item.to_dict()
    filename = f"sue-{payload['store_code']}.pdf"
    return _pdf_response(render_store_report(payload, categories), filename)


@router.get(
    "/profitability/{store_id}/categories",
    response_model=list[CategoryRowOut],
    tags=["рентабельность"],
)
def profitability_categories(store_id: int, db: DbSession, period: Period) -> list[dict[str, Any]]:
    if db.get(Store, store_id) is None:
        raise api_error(
            status.HTTP_404_NOT_FOUND, "not_found", "Торговая точка не найдена", store_id=store_id
        )
    return category_breakdown(db, store_id, *period)


# --- витрина для внешней отчётности -------------------------------------------


@router.get("/mart/kpi", response_model=list[MartRowOut], tags=["витрина"])
def mart_kpi(db: DbSession, period: Period) -> list[dict[str, Any]]:
    """Плоская витрина показателей для передачи во внешнюю систему отчётности.

    В отличие от ``/api/profitability`` показатели выдаются скалярами: инструменты
    отчётности не разбирают вложенные объекты происхождения. Условность величин при этом
    не теряется — она передана полями ``cost_source`` и долями учётной и моделируемой
    себестоимости.
    """
    return kpi_mart(db, *period)


@router.get("/mart/kpi.csv", tags=["витрина"])
def mart_kpi_csv(db: DbSession, period: Period) -> Response:
    """Та же витрина файлом — путь загрузки в отчётность, не имеющую доступа к API."""
    return _csv_response(kpi_mart(db, *period), MART_COLUMNS, "kpi_mart.csv")


# --- загрузка данных ----------------------------------------------------------


@router.get("/etl/runs", response_model=Page[EtlRunOut], tags=["загрузка"])
def etl_runs(
    db: DbSession,
    paging: Paging,
    run_status: Annotated[
        str | None, Query(alias="status", description="Фильтр по статусу")
    ] = None,
) -> Page[EtlRunOut]:
    limit, offset = paging
    count_stmt = select(func.count(EtlRun.id))
    stmt = select(EtlRun).order_by(EtlRun.id.desc())
    if run_status:
        count_stmt = count_stmt.where(EtlRun.status == run_status)
        stmt = stmt.where(EtlRun.status == run_status)

    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.limit(limit).offset(offset)).all()
    return Page[EtlRunOut](
        items=[EtlRunOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/etl/runs/{run_id}", response_model=EtlRunOut, tags=["загрузка"])
def etl_run(run_id: int, db: DbSession) -> EtlRunOut:
    run = db.get(EtlRun, run_id)
    if run is None:
        raise api_error(
            status.HTTP_404_NOT_FOUND, "not_found", "Загрузка не найдена", run_id=run_id
        )
    return EtlRunOut.model_validate(run)


@router.get("/etl/runs/{run_id}/errors", response_model=Page[EtlErrorOut], tags=["загрузка"])
def etl_errors(run_id: int, db: DbSession, paging: Paging) -> Page[EtlErrorOut]:
    if db.get(EtlRun, run_id) is None:
        raise api_error(
            status.HTTP_404_NOT_FOUND, "not_found", "Загрузка не найдена", run_id=run_id
        )
    limit, offset = paging
    total = db.scalar(select(func.count(EtlError.id)).where(EtlError.run_id == run_id)) or 0
    rows = db.scalars(
        select(EtlError)
        .where(EtlError.run_id == run_id)
        .order_by(EtlError.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return Page[EtlErrorOut](
        items=[EtlErrorOut.model_validate(e) for e in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/etl/import",
    response_model=EtlRunOut,
    status_code=status.HTTP_201_CREATED,
    tags=["загрузка"],
    dependencies=[WriteAccess],
)
def etl_import(body: ImportRequest, db: DbSession, settings: Config) -> EtlRunOut:
    """Загрузить пакет обмена, лежащий в каталоге фикстур на сервере."""
    if not settings.allow_local_path_import:
        raise api_error(
            status.HTTP_403_FORBIDDEN,
            "local_import_disabled",
            "Импорт по локальному пути отключён; используйте POST /api/etl/upload",
        )
    try:
        path = resolve_import_path(settings.fixtures_dir, body.path)
    except UnsafePathError as exc:
        raise api_error(status.HTTP_400_BAD_REQUEST, "unsafe_path", str(exc)) from exc
    except FileNotFoundError as exc:
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "path_not_found",
            "Путь не найден в каталоге фикстур",
            path=body.path,
        ) from exc

    run = EtlPipeline(db).run_file(path, dry_run=body.dry_run)
    return EtlRunOut.model_validate(run)


@router.post(
    "/etl/upload",
    response_model=EtlRunOut,
    status_code=status.HTTP_201_CREATED,
    tags=["загрузка"],
    dependencies=[WriteAccess],
)
async def etl_upload(
    db: DbSession,
    settings: Config,
    file: Annotated[UploadFile, File(description="JSON-файл пакета обмена")],
    dry_run: Annotated[bool, Query(description="Только проверить, не сохранять")] = False,
) -> EtlRunOut:
    """Загрузить пакет обмена файлом — основной путь передачи данных из 1С."""
    content = await file.read()
    if not content:
        raise api_error(status.HTTP_400_BAD_REQUEST, "empty_file", "Файл пуст")
    if len(content) > settings.max_upload_bytes:
        raise api_error(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "file_too_large",
            "Размер файла превышает допустимый",
            size_bytes=len(content),
            limit_bytes=settings.max_upload_bytes,
        )

    filename = file.filename or "upload.json"
    try:
        source = UploadedFileSource(filename, content)
    except SourceError as exc:
        raise api_error(status.HTTP_400_BAD_REQUEST, "invalid_source", str(exc)) from exc

    run = EtlPipeline(db).run_source(source, fallback_label=filename, dry_run=dry_run)
    return EtlRunOut.model_validate(run)


@router.post(
    "/etl/pull",
    response_model=EtlRunOut,
    status_code=status.HTTP_201_CREATED,
    tags=["загрузка"],
    dependencies=[WriteAccess],
)
def etl_pull(body: PullRequest, db: DbSession, settings: Config) -> EtlRunOut:
    """Забрать пакет(ы) с эмулятора выгрузки 1С и прогнать штатный ETL."""
    from sue.emulator.catalog import CatalogSource, EmulatorCatalog, EmulatorError

    url = (body.url or "").strip()
    builtin = not url or url in {"local", "emulator"} or url.rstrip("/").endswith("/emulator/1c")
    if builtin:
        if not settings.enable_emulator:
            raise api_error(
                status.HTTP_404_NOT_FOUND,
                "emulator_disabled",
                "Эмулятор выгрузки выключен настройкой SUE_ENABLE_EMULATOR",
            )
        try:
            batches = EmulatorCatalog(settings.fixtures_dir).iter_batches(
                body.scenario, body.exchange_id
            )
        except EmulatorError as exc:
            raise api_error(status.HTTP_404_NOT_FOUND, "emulator_not_found", str(exc)) from exc
        run = EtlPipeline(db).run_source(
            CatalogSource(batches, f"emulator:{body.scenario}"),
            fallback_label=f"emulator:{body.scenario}",
            dry_run=body.dry_run,
        )
        return EtlRunOut.model_validate(run)

    try:
        source = HttpExchangeSource(url, scenario=body.scenario, exchange_id=body.exchange_id)
    except UnsafeUrlError as exc:
        raise api_error(status.HTTP_400_BAD_REQUEST, "unsafe_url", str(exc)) from exc
    run = EtlPipeline(db).run_source(source, fallback_label=url, dry_run=body.dry_run)
    return EtlRunOut.model_validate(run)


# --- сверка -------------------------------------------------------------------


@router.get("/reconciliation", response_model=ReconciliationOut, tags=["сверка"])
def reconciliation(
    db: DbSession,
    settings: Config,
    path: Annotated[
        str | None, Query(description="Каталог или файл внутри каталога фикстур")
    ] = None,
) -> dict[str, Any]:
    """Сверить контрольные суммы файлов обмена с содержимым СУЭ."""
    try:
        source = resolve_import_path(settings.fixtures_dir, path)
    except UnsafePathError as exc:
        raise api_error(status.HTTP_400_BAD_REQUEST, "unsafe_path", str(exc)) from exc
    except FileNotFoundError as exc:
        raise api_error(
            status.HTTP_404_NOT_FOUND, "path_not_found", "Путь не найден", path=path
        ) from exc

    try:
        return reconcile(db, source, label=path or ".")
    except SourceError as exc:
        raise api_error(status.HTTP_400_BAD_REQUEST, "invalid_source", str(exc)) from exc
