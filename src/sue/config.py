"""Настройки приложения.

Все параметры читаются из переменных окружения с префиксом ``SUE_`` либо из файла ``.env``.
Некорректная конфигурация приводит к отказу на старте, а не к неверным расчётам.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
AppEnv = Literal["local", "test", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SUE_",
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: AppEnv = "local"
    log_level: LogLevel = "INFO"
    log_json: bool = False

    database_url: str = f"sqlite:///{(ROOT / 'data' / 'sue.db').as_posix()}"
    sql_echo: bool = False

    fixtures_dir: Path = ROOT / "data" / "fixtures"
    schema_path: Path = ROOT / "schemas" / "1c_retail_exchange.schema.json"

    # Загрузка данных
    max_upload_bytes: int = Field(default=32 * 1024 * 1024, ge=1024)
    max_batch_documents: int = Field(default=200_000, ge=1)
    max_errors_per_run: int = Field(default=500, ge=1)
    etl_chunk_size: int = Field(default=5_000, ge=100)
    allow_local_path_import: bool = Field(
        default=True,
        description=(
            "Разрешить импорт по пути на сервере. Пути ограничены каталогом фикстур; "
            "в production рекомендуется False и загрузка файлом."
        ),
    )

    # Моделируемые параметры рентабельности
    overhead_rate: float = Field(default=0.12, ge=0.0, le=1.0)
    default_cost_ratio: float = Field(default=0.65, gt=0.0, le=1.0)

    # Пороги оценки операционной рентабельности (светофор в витрине и интерфейсе).
    # Это управленческие ориентиры, а не учётные данные: помечаются как моделируемые.
    # Значения по умолчанию подобраны под демонстрационный набор, где рентабельность точек
    # лежит в узком диапазоне; в эксплуатации целевое значение задаёт финансовая служба.
    margin_target_pct: float = Field(default=28.0, ge=0.0, le=100.0)
    margin_warn_pct: float = Field(default=27.0, ge=0.0, le=100.0)

    api_page_size: int = Field(default=50, ge=1, le=500)
    api_max_page_size: int = Field(default=500, ge=1, le=5000)

    # Контроль доступа. Пустой ключ — барьер выключен (режим локальной демонстрации):
    # включать его по умолчанию нельзя, иначе запуск «одной командой» перестанет работать.
    api_key: str = Field(default="", description="Ключ для заголовка X-API-Key")
    protect_read: bool = Field(
        default=False,
        description="Требовать ключ и на операциях чтения, а не только на загрузке данных",
    )

    reports_dir: Path = ROOT / "data" / "reports"

    # Эмулятор выгрузки 1С (HTTP-сервис /hs/exchange). В docker-compose.prod.yml выключен.
    enable_emulator: bool = True
    emulator_allowed_hosts: str = "127.0.0.1,localhost,::1"

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, value: str) -> str:
        allowed = ("sqlite:///", "sqlite+pysqlite:///", "postgresql://", "postgresql+psycopg2://")
        if not value.startswith(allowed):
            raise ValueError(
                "SUE_DATABASE_URL должен использовать sqlite или postgresql; получено: " + value
            )
        return value

    @field_validator("fixtures_dir", "schema_path", "reports_dir")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return value if value.is_absolute() else (ROOT / value).resolve()

    @model_validator(mode="after")
    def _check_settings(self) -> Settings:
        if self.api_page_size > self.api_max_page_size:
            raise ValueError("SUE_API_PAGE_SIZE не может превышать SUE_API_MAX_PAGE_SIZE")
        if self.margin_warn_pct > self.margin_target_pct:
            raise ValueError(
                "SUE_MARGIN_WARN_PCT не может превышать SUE_MARGIN_TARGET_PCT: "
                f"{self.margin_warn_pct} > {self.margin_target_pct}"
            )
        if self.protect_read and not self.api_key:
            raise ValueError("SUE_PROTECT_READ=true требует заданного SUE_API_KEY")
        return self

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def emulator_host_list(self) -> tuple[str, ...]:
        return tuple(
            host.strip().lower() for host in self.emulator_allowed_hosts.split(",") if host.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    """Сбросить кэш настроек (используется в тестах при подмене окружения)."""
    get_settings.cache_clear()
