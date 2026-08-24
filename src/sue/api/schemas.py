"""Контракты HTTP-API. Ответы описаны моделями, чтобы схема OpenAPI совпадала с фактом."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

T = TypeVar("T")


def _as_utc(value: datetime) -> datetime:
    """Отметки времени пишутся в UTC, но SQLite возвращает их без смещения.

    Без этой нормализации один и тот же прогон выглядел бы по-разному на SQLite
    и на PostgreSQL, а клиент не мог бы отличить UTC от местного времени.
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]

ProvenanceSource = Literal["accounting", "modeled", "derived", "accounting+modeled"]
RunStatus = Literal["started", "success", "partial", "failed"]


class ErrorResponse(BaseModel):
    """Единый формат ошибки для всех эндпоинтов."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": {
                    "code": "not_found",
                    "message": "Торговая точка не найдена",
                    "details": {"store_id": 42},
                }
            }
        }
    )

    error: ErrorBody


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int = Field(description="Всего записей, удовлетворяющих фильтру")
    limit: int
    offset: int


class VersionOut(BaseModel):
    name: str
    version: str
    contract_version: str
    app_env: str
    database: str = Field(description="Тип СУБД: sqlite или postgresql")


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, str] = Field(default_factory=dict)


class StoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_ref: str
    code: str
    name: str
    city: str | None
    store_format: str | None
    is_active: bool


class ProvenanceValueOut(BaseModel):
    value: float
    source: ProvenanceSource
    note: str = ""
    unit: str = "RUB"


class ProfitabilityOut(BaseModel):
    store_id: int
    store_code: str
    store_name: str
    city: str | None
    period_from: date
    period_to: date
    overhead_rate: float = Field(description="Применённая ставка накладных расходов, %")
    lines: int
    lines_without_cost: int
    cost_accounting_share_pct: float
    cost_modeled_share_pct: float

    revenue: ProvenanceValueOut
    gross_revenue: ProvenanceValueOut
    returns: ProvenanceValueOut
    cost: ProvenanceValueOut
    cost_accounting: ProvenanceValueOut
    cost_modeled: ProvenanceValueOut
    gross_profit: ProvenanceValueOut
    gross_margin_pct: ProvenanceValueOut
    overhead: ProvenanceValueOut
    operating_profit: ProvenanceValueOut
    operating_margin_pct: ProvenanceValueOut
    quantity: ProvenanceValueOut


MarginStatus = Literal["ok", "warn", "bad"]


class MartRowOut(BaseModel):
    """Строка витрины: показатели скалярами, пригодно для загрузки во внешнюю отчётность."""

    store_id: int
    store_code: str
    store_name: str
    city: str | None
    period_from: date
    period_to: date
    gross_revenue: float
    returns: float
    revenue: float
    quantity: float
    cost: float
    cost_accounting: float
    cost_modeled: float
    gross_profit: float
    gross_margin_pct: float
    overhead: float
    overhead_rate_pct: float
    operating_profit: float
    operating_margin_pct: float
    cost_accounting_share_pct: float
    cost_modeled_share_pct: float
    lines: int
    lines_without_cost: int
    cost_source: ProvenanceSource = Field(
        description="Происхождение себестоимости: учёт, модель или их сочетание"
    )
    margin_status: MarginStatus = Field(
        description="Оценка операционной рентабельности по управленческим порогам"
    )


class DeltaOut(BaseModel):
    base: float
    compare: float
    absolute: float
    relative_pct: float | None = Field(
        default=None, description="None, если базовое значение равно нулю"
    )


class MarginDeltaOut(BaseModel):
    base: float
    compare: float
    absolute_pp: float = Field(description="Изменение в процентных пунктах")


class PeriodOut(BaseModel):
    date_from: date = Field(alias="from")
    date_to: date = Field(alias="to")

    model_config = ConfigDict(populate_by_name=True)


class CompareRowOut(BaseModel):
    store_id: int
    store_code: str
    store_name: str
    has_base: bool = Field(description="Есть ли документы в базовом периоде")
    has_compare: bool = Field(description="Есть ли документы в сравниваемом периоде")
    base_period: PeriodOut
    compare_period: PeriodOut
    metrics: dict[str, DeltaOut]
    operating_margin_pct: MarginDeltaOut
    margin_status: MarginStatus


class CategoryRowOut(BaseModel):
    category: str
    revenue: float
    cost: float
    gross_profit: float
    gross_margin_pct: float
    lines: int


class EtlRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_system: str
    source_file: str
    source_hash: str | None
    exchange_id: str | None
    contract_version: str | None
    status: RunStatus
    dry_run: bool
    started_at: UtcDatetime
    finished_at: UtcDatetime | None
    duration_ms: int | None
    stores_upserted: int
    products_upserted: int
    documents_accepted: int
    documents_rejected: int
    lines_accepted: int
    lines_rejected: int
    errors_count: int
    message: str | None


class EtlErrorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    stage: str
    severity: str
    entity: str | None
    source_ref: str | None
    location: str | None
    detail: str
    created_at: UtcDatetime


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "Имя файла или подкаталога внутри каталога фикстур (SUE_FIXTURES_DIR). "
            "Абсолютные пути и переходы вверх запрещены. По умолчанию — accounting."
        ),
    )
    dry_run: bool = Field(
        default=False, description="Проверить пакет и посчитать итоги, ничего не сохраняя"
    )


class PullRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "Адрес эмулятора (например http://127.0.0.1:8001). "
            "Пустое значение — встроенный эмулятор этого же приложения."
        ),
    )
    scenario: str = Field(default="accounting", max_length=64)
    exchange_id: str | None = Field(
        default=None,
        max_length=128,
        description="Идентификатор одного пакета; иначе загружается весь сценарий",
    )
    dry_run: bool = False


class TotalsOut(BaseModel):
    stores: int
    products: int
    documents: int
    lines: int
    revenue: float
    quantity: float
    cost_accounting: float


class ReconciliationOut(BaseModel):
    source: str
    source_totals: TotalsOut
    db_totals: TotalsOut
    diff_minor_units: dict[str, int]
    matched: bool


ErrorResponse.model_rebuild()
