"""Прогон ядра на PostgreSQL.

Тесты помечены маркером ``pg`` и выполняются только если задан ``SUE_TEST_PG_URL``
(в CI — сервис-контейнер PostgreSQL). Без этого «работает на SQLite» не является
доказательством переносимости: целочисленное хранение денег и агрегация средствами
СУБД должны давать тот же результат на обеих платформах.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, text
from sqlalchemy.orm import Session, sessionmaker

from sue.adapter_1c import Batch
from sue.db import Base
from sue.db import models as m  # noqa: F401  — регистрация таблиц в metadata
from sue.domain.profitability import compute_store_profitability
from sue.domain.reconciliation import db_totals, reconcile
from sue.etl.pipeline import EtlPipeline
from tests import factories as f

PG_URL = os.environ.get("SUE_TEST_PG_URL")

pytestmark = [
    pytest.mark.pg,
    pytest.mark.skipif(not PG_URL, reason="SUE_TEST_PG_URL не задан"),
]


@pytest.fixture
def pg_session() -> Iterator[Session]:
    engine = create_engine(str(PG_URL), future=True, pool_pre_ping=True)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, future=True)
    session = factory()
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(delete(table))
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_connection_is_postgresql(pg_session: Session) -> None:
    version = pg_session.execute(text("SELECT version()")).scalar_one()
    assert "PostgreSQL" in str(version)


def test_etl_and_reconciliation_match_on_postgres(pg_session: Session, fixtures_dir: Path) -> None:
    run = EtlPipeline(pg_session).run_file(fixtures_dir / "main")
    assert run.status == "success"

    report = reconcile(pg_session, fixtures_dir / "main")
    assert report["matched"] is True
    assert set(report["diff_minor_units"].values()) == {0}


def test_kpi_are_identical_to_sqlite(pg_session: Session, db: Session) -> None:
    """Один и тот же батч на двух СУБД должен дать побитово равные показатели."""
    payload = f.batch(
        documents=[
            f.document("d1", lines=[f.line("l1", 1234.56, quantity=3.0, cost=800.10)]),
            f.document("d2", lines=[f.line("l2", 99.99)]),
            f.document(
                "d3",
                lines=[f.line("l3", 250.00, quantity=2.0, cost=150.00)],
                doc_type="return",
            ),
        ]
    )
    EtlPipeline(pg_session).run_batch(Batch("pg", payload))
    EtlPipeline(db).run_batch(Batch("sqlite", payload))

    assert db_totals(pg_session) == db_totals(db)

    from sue.db.models import Store

    pg_store = pg_session.query(Store).one()
    lite_store = db.query(Store).one()
    pg_kpi = compute_store_profitability(pg_session, pg_store.id)
    lite_kpi = compute_store_profitability(db, lite_store.id)
    assert pg_kpi is not None and lite_kpi is not None
    for key in ("revenue", "cost", "gross_profit", "overhead", "operating_profit"):
        assert pg_kpi[key].value == lite_kpi[key].value, key


def test_repeated_load_is_idempotent_on_postgres(pg_session: Session, fixtures_dir: Path) -> None:
    pipeline = EtlPipeline(pg_session)
    pipeline.run_file(fixtures_dir / "main")
    first = db_totals(pg_session)
    pipeline.run_file(fixtures_dir / "main")
    assert db_totals(pg_session) == first
