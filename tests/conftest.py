"""Общая настройка тестов.

Тесты работают на изолированной временной БД и на собственном, небольшом наборе
сгенерированных данных: репозиторный ``data/fixtures`` не читается и не изменяется.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="sue-tests-"))
FIXTURES_DIR = _TMP_ROOT / "fixtures"


def pytest_configure(config: pytest.Config) -> None:
    os.environ["SUE_APP_ENV"] = "test"
    os.environ["SUE_DATABASE_URL"] = f"sqlite:///{(_TMP_ROOT / 'test.db').as_posix()}"
    os.environ["SUE_FIXTURES_DIR"] = str(FIXTURES_DIR)
    os.environ["SUE_LOG_LEVEL"] = "WARNING"

    from sue.config import reset_settings
    from sue.db import reset_engine

    reset_settings()
    reset_engine()


def pytest_unconfigure(config: pytest.Config) -> None:
    from sue.db import reset_engine

    reset_engine()
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


# --- данные -------------------------------------------------------------------


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Небольшой набор сценариев, достаточный для проверки всех веток поведения."""
    from sue.datagen.generator import ScenarioSpec
    from sue.datagen.scenarios import write_invalid_examples, write_modeled_params, write_scenario

    specs = [
        ScenarioSpec(
            name="main",
            description="Две точки, 26 недель, смешанная себестоимость, возвраты",
            seed=7,
            store_refs=("store-01", "store-02"),
            weeks_override=26,
            docs_per_week=(3, 5),
            cost_coverage=0.75,
            return_rate=0.06,
        ),
        ScenarioSpec(
            name="short",
            description="Короткая история: новая точка с малым объёмом документов",
            seed=8,
            store_refs=("store-04",),
            weeks_override=6,
            docs_per_week=(2, 3),
            cost_coverage=1.0,
            return_rate=0.0,
            split_by_month=False,
        ),
        ScenarioSpec(
            name="nocost",
            description="Себестоимость отсутствует полностью",
            seed=9,
            store_refs=("store-03",),
            weeks_override=20,
            docs_per_week=(2, 3),
            cost_coverage=0.0,
            return_rate=0.0,
            split_by_month=False,
        ),
    ]

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    write_modeled_params(FIXTURES_DIR)
    write_invalid_examples(FIXTURES_DIR)
    for spec in specs:
        write_scenario(spec, FIXTURES_DIR)
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def _schema(fixtures_dir: Path) -> None:
    from sue.db import create_schema

    create_schema()


@pytest.fixture
def db(_schema: None) -> Iterator[Session]:
    """Чистая сессия: перед каждым тестом таблицы пусты."""
    from sqlalchemy import delete

    from sue.db import Base, get_session_factory
    from sue.domain.profitability import _cost_ratios_cached

    session = get_session_factory()()
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(delete(table))
    session.commit()
    _cost_ratios_cached.cache_clear()

    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def loaded_db(db: Session, fixtures_dir: Path) -> Session:
    """Сессия с загруженным основным сценарием."""
    from sue.etl.pipeline import EtlPipeline

    EtlPipeline(db).run_file(fixtures_dir / "main")
    return db


@pytest.fixture
def client(loaded_db: Session, fixtures_dir: Path) -> Iterator[TestClient]:
    from fastapi.testclient import TestClient

    from sue.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def empty_client(db: Session, fixtures_dir: Path) -> Iterator[TestClient]:
    from fastapi.testclient import TestClient

    from sue.main import app

    with TestClient(app) as test_client:
        yield test_client
