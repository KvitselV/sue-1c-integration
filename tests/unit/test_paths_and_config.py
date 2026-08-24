"""Безопасность путей импорта и валидация конфигурации."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sue.api.paths import UnsafePathError, resolve_import_path
from sue.config import Settings


@pytest.fixture
def base(tmp_path: Path) -> Path:
    (tmp_path / "accounting").mkdir()
    (tmp_path / "accounting" / "batch.json").write_text("{}", encoding="utf-8")
    (tmp_path.parent / "secret.txt").write_text("секрет", encoding="utf-8")
    return tmp_path


def test_default_points_to_accounting(base: Path) -> None:
    assert resolve_import_path(base, None) == (base / "accounting").resolve()


def test_relative_file_inside_base_is_allowed(base: Path) -> None:
    assert resolve_import_path(base, "accounting/batch.json").name == "batch.json"


@pytest.mark.parametrize(
    "candidate",
    [
        "../secret.txt",
        "accounting/../../secret.txt",
        "..",
        "C:/Windows/win.ini",
        "/etc/passwd",
        "\\\\server\\share\\file.json",
    ],
)
def test_escaping_base_directory_is_rejected(base: Path, candidate: str) -> None:
    with pytest.raises(UnsafePathError):
        resolve_import_path(base, candidate)


def test_missing_path_raises_file_not_found(base: Path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_import_path(base, "accounting/absent.json")


# --- конфигурация -------------------------------------------------------------


def test_rejects_unsupported_database_backend() -> None:
    with pytest.raises(ValidationError, match="sqlite или postgresql"):
        Settings(database_url="mysql://localhost/sue")


@pytest.mark.parametrize(
    ("field", "value"),
    [("overhead_rate", 1.5), ("overhead_rate", -0.1), ("default_cost_ratio", 0.0)],
)
def test_rejects_out_of_range_model_parameters(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_relative_paths_are_resolved_to_absolute() -> None:
    settings = Settings(fixtures_dir=Path("data/fixtures"))
    assert settings.fixtures_dir.is_absolute()
    assert Settings(reports_dir=Path("data/reports")).reports_dir.is_absolute()


def test_rejects_warning_threshold_above_target() -> None:
    """Порог предупреждения выше целевого сделал бы оценку рентабельности бессмысленной."""
    with pytest.raises(ValidationError, match="SUE_MARGIN_WARN_PCT"):
        Settings(margin_target_pct=15.0, margin_warn_pct=25.0)


def test_access_barrier_is_disabled_by_default() -> None:
    settings = Settings()
    assert settings.api_key == ""
    assert settings.auth_enabled is False
    assert settings.protect_read is False


def test_key_enables_access_barrier() -> None:
    assert Settings(api_key="ключ").auth_enabled is True


def test_emulator_hosts_are_loopback_by_default() -> None:
    settings = Settings()
    assert settings.enable_emulator is True
    assert settings.emulator_host_list == ("127.0.0.1", "localhost", "::1")


def test_symlink_escape_is_rejected(base: Path, tmp_path: Path) -> None:
    target = tmp_path.parent / "outside.json"
    target.write_text("{}", encoding="utf-8")
    link = base / "leak.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("символические ссылки недоступны в этой среде")
    with pytest.raises(UnsafePathError):
        resolve_import_path(base, "leak.json")


def test_prepare_schema_fails_in_production_without_tables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from sue.config import reset_settings
    from sue.db import prepare_schema, reset_engine

    monkeypatch.setenv("SUE_DATABASE_URL", f"sqlite:///{(tmp_path / 'empty.db').as_posix()}")
    reset_settings()
    reset_engine()
    try:
        with pytest.raises(RuntimeError, match="alembic"):
            prepare_schema(production=True)
    finally:
        reset_settings()
        reset_engine()
