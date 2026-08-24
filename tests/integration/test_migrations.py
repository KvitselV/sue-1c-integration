"""Миграции должны разворачивать ту же схему, что описана моделями."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def alembic_config(tmp_path: Path) -> tuple[Config, str]:
    url = f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))

    previous = os.environ.get("SUE_DATABASE_URL")
    os.environ["SUE_DATABASE_URL"] = url

    from sue.config import reset_settings
    from sue.db import reset_engine

    reset_settings()
    reset_engine()
    try:
        yield config, url
    finally:
        reset_engine()
        if previous is None:
            os.environ.pop("SUE_DATABASE_URL", None)
        else:
            os.environ["SUE_DATABASE_URL"] = previous
        reset_settings()
        reset_engine()


def test_upgrade_creates_all_model_tables(alembic_config) -> None:
    config, url = alembic_config
    command.upgrade(config, "head")

    from sue.db import (
        Base,
        models,  # noqa: F401
    )

    inspector = inspect(create_engine(url))
    created = set(inspector.get_table_names()) - {"alembic_version"}
    assert created == set(Base.metadata.tables)


def test_downgrade_removes_all_tables(alembic_config) -> None:
    config, url = alembic_config
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    inspector = inspect(create_engine(url))
    assert set(inspector.get_table_names()) - {"alembic_version"} == set()


def test_money_columns_are_integer_typed(alembic_config) -> None:
    """Суммы должны храниться целыми: это исключает ошибки округления float."""
    config, url = alembic_config
    command.upgrade(config, "head")

    columns = {c["name"]: c for c in inspect(create_engine(url)).get_columns("sale_lines")}
    for name in ("revenue_kopecks", "cost_accounting_kopecks", "quantity_milli"):
        assert "INT" in str(columns[name]["type"]).upper(), name
