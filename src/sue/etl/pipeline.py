"""ETL-конвейер загрузки пакетов обмена в СУЭ.

Свойства реализации:

* **Идемпотентность.** Ключ — ``source_ref``. Повторная загрузка того же пакета
  не создаёт дублей: заголовки документов обновляются, строки заменяются целиком.
* **Аудит выживает при отказе.** Запись о загрузке фиксируется до начала записи
  данных, поэтому откат транзакции с данными не уничтожает историю попытки.
* **Пакетная запись.** Существующие объекты вычитываются одним запросом на тип,
  вставка выполняется порциями (``etl_chunk_size``), а не построчно.
* **Возвраты.** Документ с ``documentType="return"`` пишется с отрицательными
  суммами и количеством, поэтому агрегаты сразу дают выручку за вычетом возвратов.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import delete, insert, select, update
from sqlalchemy.orm import Session

from sue.adapter_1c import (
    Batch,
    ContractValidator,
    FileExchangeSource,
    OneCSource,
    SourceError,
    ValidationIssue,
)
from sue.config import get_settings
from sue.db.models import (
    DOC_TYPE_RETURN,
    DOC_TYPE_SALE,
    RUN_STATUS_FAILED,
    RUN_STATUS_PARTIAL,
    RUN_STATUS_SUCCESS,
    EtlError,
    EtlRun,
    Product,
    SaleDocument,
    SaleLine,
    Store,
    utcnow,
)
from sue.money import to_kopecks, to_milli

logger = logging.getLogger(__name__)

# SQLite ограничивает число параметров в запросе; порции IN-условий держим ниже лимита.
_IN_CHUNK = 400

T = TypeVar("T")


def _chunks(items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


@dataclass
class LoadStats:
    stores_upserted: int = 0
    products_upserted: int = 0
    documents_accepted: int = 0
    documents_rejected: int = 0
    lines_accepted: int = 0
    lines_rejected: int = 0
    revenue_kopecks: int = 0
    issues: list[ValidationIssue] = field(default_factory=list)


class EtlPipeline:
    def __init__(
        self,
        db: Session,
        schema_path: Path | None = None,
        *,
        chunk_size: int | None = None,
    ) -> None:
        settings = get_settings()
        self.db = db
        self.settings = settings
        self.validator = ContractValidator(schema_path or settings.schema_path)
        self.chunk_size = chunk_size or settings.etl_chunk_size

    # --- публичный интерфейс --------------------------------------------------

    def run_file(self, path: Path | str, *, dry_run: bool = False) -> EtlRun:
        """Загрузить файл или все файлы каталога. Возвращает запись о последней попытке."""
        return self.run_source(FileExchangeSource(path), fallback_label=str(path), dry_run=dry_run)

    def run_source(
        self,
        source: OneCSource,
        *,
        fallback_label: str = "<источник>",
        dry_run: bool = False,
    ) -> EtlRun:
        runs = self.run_batches(source, fallback_label=fallback_label, dry_run=dry_run)
        return runs[-1]

    def run_batches(
        self,
        source: OneCSource,
        *,
        fallback_label: str = "<источник>",
        dry_run: bool = False,
    ) -> list[EtlRun]:
        runs: list[EtlRun] = []
        try:
            batches: Iterable[Batch] = list(source.iter_batches())
        except SourceError as exc:
            return [self._failed_run(fallback_label, str(exc), dry_run=dry_run)]

        if not batches:
            return [
                self._failed_run(
                    fallback_label,
                    "Нет JSON-файлов, соответствующих контракту обмена",
                    dry_run=dry_run,
                )
            ]

        for batch in batches:
            runs.append(self.run_batch(batch, dry_run=dry_run))
        return runs

    def run_batch(self, batch: Batch, *, dry_run: bool = False) -> EtlRun:
        started = time.perf_counter()
        run = self._start_run(batch, dry_run=dry_run)

        issues = self.validator.validate_schema(batch.payload)
        if not issues:
            issues = self.validator.validate_rules(batch.payload)
        blocking = [i for i in issues if i.severity == "error"]

        if blocking:
            self._finish_run(
                run,
                status=RUN_STATUS_FAILED,
                message=f"Пакет отклонён на валидации: нарушений — {len(blocking)}",
                issues=issues,
                started=started,
            )
            logger.warning(
                "Пакет обмена отклонён",
                extra={"run_id": run.id, "source": batch.label, "issues": len(blocking)},
            )
            return run

        try:
            stats = self._load(batch, run_id=run.id, commit=not dry_run)
        except Exception as exc:
            self.db.rollback()
            logger.exception("Сбой загрузки пакета", extra={"run_id": run.id})
            self._finish_run(
                run,
                status=RUN_STATUS_FAILED,
                message=f"Сбой загрузки: {type(exc).__name__}: {exc}",
                issues=issues,
                started=started,
                stage="load",
            )
            return run

        stats.issues = issues + stats.issues
        if dry_run:
            self.db.rollback()

        rejected = stats.documents_rejected + stats.lines_rejected
        status = RUN_STATUS_SUCCESS if rejected == 0 else RUN_STATUS_PARTIAL
        self._finish_run(
            run,
            status=status,
            message=self._summary_message(batch, stats, dry_run=dry_run),
            issues=stats.issues,
            started=started,
            stats=stats,
        )
        logger.info(
            "Пакет обмена загружен",
            extra={
                "run_id": run.id,
                "source": batch.label,
                "status": status,
                "documents": stats.documents_accepted,
                "lines": stats.lines_accepted,
                "dry_run": dry_run,
            },
        )
        return run

    # --- работа с записью аудита ---------------------------------------------

    def _start_run(self, batch: Batch, *, dry_run: bool) -> EtlRun:
        run = EtlRun(
            source_file=batch.label,
            source_hash=batch.content_hash or None,
            exchange_id=batch.exchange_id,
            contract_version=batch.contract_version,
            dry_run=dry_run,
            started_at=utcnow(),
        )
        self.db.add(run)
        # Фиксируем попытку сразу: аудит не должен исчезнуть при откате загрузки данных.
        self.db.commit()
        self.db.refresh(run)
        return run

    def _failed_run(self, label: str, message: str, *, dry_run: bool) -> EtlRun:
        run = EtlRun(
            source_file=label,
            status=RUN_STATUS_FAILED,
            dry_run=dry_run,
            started_at=utcnow(),
            finished_at=utcnow(),
            duration_ms=0,
            message=message,
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def _finish_run(
        self,
        run: EtlRun,
        *,
        status: str,
        message: str,
        issues: Sequence[ValidationIssue],
        started: float,
        stats: LoadStats | None = None,
        stage: str | None = None,
    ) -> None:
        limit = self.settings.max_errors_per_run
        for issue in issues[:limit]:
            self.db.add(
                EtlError(
                    run_id=run.id,
                    stage=stage or issue.stage,
                    severity=issue.severity,
                    entity=issue.entity,
                    source_ref=issue.source_ref,
                    location=issue.location,
                    detail=issue.detail,
                )
            )
        if len(issues) > limit:
            self.db.add(
                EtlError(
                    run_id=run.id,
                    stage="audit",
                    severity="warning",
                    entity="batch",
                    location="<batch>",
                    detail=(
                        f"Показаны первые {limit} нарушений из {len(issues)}; "
                        "остальные не сохранены (SUE_MAX_ERRORS_PER_RUN)"
                    ),
                )
            )

        run.status = status
        run.message = message
        run.finished_at = utcnow()
        run.duration_ms = int((time.perf_counter() - started) * 1000)
        run.errors_count = len(issues)
        if stats is not None:
            run.stores_upserted = stats.stores_upserted
            run.products_upserted = stats.products_upserted
            run.documents_accepted = stats.documents_accepted
            run.documents_rejected = stats.documents_rejected
            run.lines_accepted = stats.lines_accepted
            run.lines_rejected = stats.lines_rejected
            run.revenue_kopecks = stats.revenue_kopecks
        self.db.commit()
        self.db.refresh(run)

    def _summary_message(self, batch: Batch, stats: LoadStats, *, dry_run: bool) -> str:
        prefix = "Проверочный прогон (данные не сохранены)" if dry_run else "Загрузка выполнена"
        return (
            f"{prefix}; exchangeId={batch.exchange_id}; "
            f"ТТ={stats.stores_upserted}; номенклатура={stats.products_upserted}; "
            f"документы={stats.documents_accepted}; строки={stats.lines_accepted}"
        )

    # --- загрузка данных ------------------------------------------------------

    def _load(self, batch: Batch, *, run_id: int, commit: bool = True) -> LoadStats:
        payload = batch.payload
        stats = LoadStats()

        store_map = self._upsert_stores(payload.get("stores", []), stats)
        product_map = self._upsert_products(payload.get("products", []), stats)
        self._upsert_documents(
            payload.get("saleDocuments", []), store_map, product_map, stats, run_id
        )

        # В проверочном прогоне транзакция не фиксируется — вызывающий её откатит.
        if commit:
            self.db.commit()
        return stats

    def _upsert_stores(self, items: list[dict[str, Any]], stats: LoadStats) -> dict[str, int]:
        if not items:
            return {}
        refs = [item["ref"] for item in items]
        existing = self._existing_ids(Store, refs)

        to_insert = []
        for item in items:
            values = {
                "source_ref": item["ref"],
                "code": item["code"],
                "name": item["name"],
                "city": item.get("city"),
                "store_format": item.get("format"),
                "is_active": item.get("isActive", True),
                "updated_at": utcnow(),
            }
            if item["ref"] in existing:
                self.db.execute(
                    update(Store).where(Store.id == existing[item["ref"]]).values(**values)
                )
            else:
                to_insert.append({**values, "loaded_at": utcnow()})

        if to_insert:
            self.db.execute(insert(Store), to_insert)
        stats.stores_upserted = len(items)
        return self._existing_ids(Store, refs)

    def _upsert_products(self, items: list[dict[str, Any]], stats: LoadStats) -> dict[str, int]:
        if not items:
            return {}
        refs = [item["ref"] for item in items]
        existing = self._existing_ids(Product, refs)

        to_insert = []
        for item in items:
            values = {
                "source_ref": item["ref"],
                "sku": item["sku"],
                "name": item["name"],
                "category": item["category"],
                "updated_at": utcnow(),
            }
            if item["ref"] in existing:
                self.db.execute(
                    update(Product).where(Product.id == existing[item["ref"]]).values(**values)
                )
            else:
                to_insert.append({**values, "loaded_at": utcnow()})

        if to_insert:
            self.db.execute(insert(Product), to_insert)
        stats.products_upserted = len(items)
        return self._existing_ids(Product, refs)

    def _upsert_documents(
        self,
        docs: list[dict[str, Any]],
        store_map: dict[str, int],
        product_map: dict[str, int],
        stats: LoadStats,
        run_id: int,
    ) -> None:
        if not docs:
            return
        if len(docs) > self.settings.max_batch_documents:
            raise SourceError(
                f"В пакете {len(docs)} документов — больше лимита "
                f"{self.settings.max_batch_documents}"
            )

        accepted_docs: list[dict[str, Any]] = []
        for i, doc in enumerate(docs):
            store_id = store_map.get(doc["storeRef"])
            if store_id is None:
                stats.documents_rejected += 1
                stats.lines_rejected += len(doc.get("lines", []))
                stats.issues.append(
                    ValidationIssue(
                        stage="load",
                        location=f"saleDocuments/{i}/storeRef",
                        entity="saleDocument",
                        source_ref=doc.get("ref"),
                        detail=f"торговая точка {doc['storeRef']!r} отсутствует в справочнике",
                    )
                )
                continue
            accepted_docs.append({**doc, "_store_id": store_id, "_index": i})

        doc_refs = [d["ref"] for d in accepted_docs]
        existing_docs = self._existing_ids(SaleDocument, doc_refs)

        new_headers = []
        for doc in accepted_docs:
            doc_type = doc.get("documentType", DOC_TYPE_SALE)
            values = {
                "source_ref": doc["ref"],
                "store_id": doc["_store_id"],
                "doc_type": doc_type,
                "doc_date": date.fromisoformat(doc["date"]),
                "doc_number": doc["number"],
                "currency": doc.get("currency", "RUB"),
                "etl_run_id": run_id,
                "updated_at": utcnow(),
            }
            if doc["ref"] in existing_docs:
                self.db.execute(
                    update(SaleDocument)
                    .where(SaleDocument.id == existing_docs[doc["ref"]])
                    .values(**values)
                )
            else:
                new_headers.append({**values, "loaded_at": utcnow()})

        for chunk in _chunks(new_headers, self.chunk_size):
            self.db.execute(insert(SaleDocument), list(chunk))

        doc_ids = self._existing_ids(SaleDocument, doc_refs)

        # Идемпотентность строк: удаляем прежние строки и по документу, и по ref строки
        # (строка могла быть перенесена между документами в новой выгрузке).
        line_refs = [line["ref"] for doc in accepted_docs for line in doc.get("lines", [])]
        for ref_chunk in _chunks(line_refs, _IN_CHUNK):
            self.db.execute(delete(SaleLine).where(SaleLine.source_ref.in_(list(ref_chunk))))
        for id_chunk in _chunks(list(doc_ids.values()), _IN_CHUNK):
            self.db.execute(delete(SaleLine).where(SaleLine.document_id.in_(list(id_chunk))))

        rows: list[dict[str, Any]] = []
        for doc in accepted_docs:
            doc_id = doc_ids[doc["ref"]]
            doc_type = doc.get("documentType", DOC_TYPE_SALE)
            sign = -1 if doc_type == DOC_TYPE_RETURN else 1
            sale_date = date.fromisoformat(doc["date"])

            for j, line in enumerate(doc.get("lines", [])):
                product_id = product_map.get(line["productRef"])
                if product_id is None:
                    stats.lines_rejected += 1
                    stats.issues.append(
                        ValidationIssue(
                            stage="load",
                            location=f"saleDocuments/{doc['_index']}/lines/{j}/productRef",
                            entity="saleLine",
                            source_ref=line.get("ref"),
                            detail=f"номенклатура {line['productRef']!r} отсутствует в справочнике",
                        )
                    )
                    continue

                revenue = sign * to_kopecks(line["amount"])
                cost = line.get("costAmount")
                rows.append(
                    {
                        "source_ref": line["ref"],
                        "document_id": doc_id,
                        "store_id": doc["_store_id"],
                        "product_id": product_id,
                        "sale_date": sale_date,
                        "doc_type": doc_type,
                        "quantity_milli": sign * to_milli(line["quantity"]),
                        "revenue_kopecks": revenue,
                        "cost_accounting_kopecks": (
                            sign * to_kopecks(cost) if cost is not None else None
                        ),
                        "loaded_at": utcnow(),
                    }
                )
                stats.lines_accepted += 1
                stats.revenue_kopecks += revenue

            stats.documents_accepted += 1

        for chunk in _chunks(rows, self.chunk_size):
            self.db.execute(insert(SaleLine), list(chunk))

    def _existing_ids(self, model: type[Any], refs: Sequence[str]) -> dict[str, int]:
        result: dict[str, int] = {}
        for chunk in _chunks(list(refs), _IN_CHUNK):
            rows = self.db.execute(
                select(model.source_ref, model.id).where(model.source_ref.in_(list(chunk)))
            ).all()
            for source_ref, object_id in rows:
                result[str(source_ref)] = int(object_id)
        return result
