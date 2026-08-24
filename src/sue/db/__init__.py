"""Подключение к БД.

Engine создаётся лениво: это позволяет тестам и CLI подменить ``SUE_DATABASE_URL``
до первого обращения и не тянуть соединение при простом импорте модулей.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, MetaData, create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from sue.config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _build_engine() -> Engine:
    settings = get_settings()
    url = settings.database_url
    kwargs: dict[str, object] = {"future": True, "echo": settings.sql_echo}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(pool_pre_ping=True, pool_size=5, max_overflow=10)

    engine = create_engine(url, **kwargs)  # type: ignore[arg-type]

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return engine


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(), autoflush=False, autocommit=False, future=True
        )
    return _session_factory


def reset_engine() -> None:
    """Закрыть текущий engine (тесты, смена конфигурации)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def create_schema() -> None:
    """Создать таблицы напрямую из моделей.

    Штатный путь развёртывания — ``alembic upgrade head``. Эта функция нужна тестам
    и быстрому локальному запуску.
    """
    from sue.db import models  # noqa: F401  — регистрация моделей в metadata

    Base.metadata.create_all(bind=get_engine())


def schema_is_ready() -> bool:
    """Есть ли прикладная схема (таблица торговых точек)."""
    return inspect(get_engine()).has_table("stores")


def prepare_schema(*, production: bool) -> None:
    """В production схему создаёт только Alembic; иначе — create_all для локального запуска."""
    if production:
        if not schema_is_ready():
            raise RuntimeError("Схема БД не создана. Выполните: alembic upgrade head")
        return
    create_schema()


def get_db() -> Generator[Session, None, None]:
    """FastAPI-зависимость: сессия на запрос."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Транзакционный блок для скриптов и фоновых задач."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
