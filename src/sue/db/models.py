"""Модель данных СУЭ.

Соглашения:

* ``source_ref`` — идентификатор объекта в системе-источнике (GUID 1С). Уникален,
  используется как ключ идемпотентности при повторной загрузке того же пакета.
* Денежные величины хранятся в копейках (``*_kopecks``), количества — в тысячных
  долях (``*_milli``). См. :mod:`sue.money`.
* Себестоимость из учётного контура (``cost_accounting_kopecks``) допускает NULL:
  отсутствие значения означает, что показатель будет смоделирован, и это явно
  отражается в происхождении KPI.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sue.db import Base

DOC_TYPE_SALE = "sale"
DOC_TYPE_RETURN = "return"
DOC_TYPES = (DOC_TYPE_SALE, DOC_TYPE_RETURN)

RUN_STATUS_STARTED = "started"
RUN_STATUS_SUCCESS = "success"
RUN_STATUS_PARTIAL = "partial"
RUN_STATUS_FAILED = "failed"
RUN_STATUSES = (RUN_STATUS_STARTED, RUN_STATUS_SUCCESS, RUN_STATUS_PARTIAL, RUN_STATUS_FAILED)


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    loaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class Store(TimestampMixin, Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_ref: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    store_format: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    documents: Mapped[list[SaleDocument]] = relationship(
        back_populates="store", cascade="all, delete-orphan"
    )


class Product(TimestampMixin, Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_ref: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(128), default="Прочее", index=True)


class SaleDocument(TimestampMixin, Base):
    """Документ реализации или возврата."""

    __tablename__ = "sale_documents"
    __table_args__ = (
        UniqueConstraint("source_ref", name="uq_sale_documents_source_ref"),
        CheckConstraint("doc_type IN ('sale', 'return')", name="doc_type"),
        Index("ix_sale_documents_store_id_doc_date", "store_id", "doc_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_ref: Mapped[str] = mapped_column(String(64))
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"))
    doc_type: Mapped[str] = mapped_column(String(16), default=DOC_TYPE_SALE)
    doc_date: Mapped[date] = mapped_column(Date, index=True)
    doc_number: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    etl_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("etl_runs.id", ondelete="SET NULL"), nullable=True
    )

    store: Mapped[Store] = relationship(back_populates="documents")
    lines: Mapped[list[SaleLine]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class SaleLine(Base):
    """Строка документа. Для возвратов количество и суммы отрицательные."""

    __tablename__ = "sale_lines"
    __table_args__ = (
        UniqueConstraint("source_ref", name="uq_sale_lines_source_ref"),
        Index("ix_sale_lines_store_id_sale_date", "store_id", "sale_date"),
        Index("ix_sale_lines_document_id", "document_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_ref: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[int] = mapped_column(ForeignKey("sale_documents.id", ondelete="CASCADE"))
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    sale_date: Mapped[date] = mapped_column(Date)
    doc_type: Mapped[str] = mapped_column(String(16), default=DOC_TYPE_SALE)
    quantity_milli: Mapped[int] = mapped_column(BigInteger)
    revenue_kopecks: Mapped[int] = mapped_column(BigInteger)
    cost_accounting_kopecks: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    loaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    document: Mapped[SaleDocument] = relationship(back_populates="lines")
    product: Mapped[Product] = relationship()


class EtlRun(Base):
    """Аудит одной загрузки пакета обмена."""

    __tablename__ = "etl_runs"
    __table_args__ = (
        CheckConstraint("status IN ('started', 'success', 'partial', 'failed')", name="status"),
        Index("ix_etl_runs_started_at", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_system: Mapped[str] = mapped_column(String(64), default="1C_Retail_FileExchange")
    source_file: Mapped[str] = mapped_column(String(512))
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exchange_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    contract_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=RUN_STATUS_STARTED, index=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    stores_upserted: Mapped[int] = mapped_column(Integer, default=0)
    products_upserted: Mapped[int] = mapped_column(Integer, default=0)
    documents_accepted: Mapped[int] = mapped_column(Integer, default=0)
    documents_rejected: Mapped[int] = mapped_column(Integer, default=0)
    lines_accepted: Mapped[int] = mapped_column(Integer, default=0)
    lines_rejected: Mapped[int] = mapped_column(Integer, default=0)
    errors_count: Mapped[int] = mapped_column(Integer, default=0)
    revenue_kopecks: Mapped[int] = mapped_column(BigInteger, default=0)

    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    errors: Mapped[list[EtlError]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def records_accepted(self) -> int:
        """Совокупный объём принятых записей (для сводок и обратной совместимости)."""
        return (
            self.stores_upserted
            + self.products_upserted
            + self.documents_accepted
            + self.lines_accepted
        )

    @property
    def records_rejected(self) -> int:
        return self.documents_rejected + self.lines_rejected


class EtlError(Base):
    __tablename__ = "etl_errors"
    __table_args__ = (Index("ix_etl_errors_run_id_stage", "run_id", "stage"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("etl_runs.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16), default="error")
    entity: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[EtlRun] = relationship(back_populates="errors")


class ModelParam(Base):
    """Параметры моделируемых показателей — то, чего нет в учётных данных."""

    __tablename__ = "model_params"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True)
    value_json: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
