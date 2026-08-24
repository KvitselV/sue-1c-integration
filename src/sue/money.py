"""Точная арифметика денег и количеств.

Денежные величины хранятся в БД целым числом минимальных единиц (копеек), количества —
в целых тысячных долях единицы. Это даёт побитово одинаковый результат на SQLite и
PostgreSQL и позволяет суммировать значения средствами СУБД без ошибок float
(`Numeric` в SQLite деградирует до float, поэтому целочисленное хранение надёжнее).

Наружу (в API, шаблоны, отчёты) значения отдаются как `Decimal`, округлённый до
2 знаков (деньги) или 3 знаков (количество).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import TypeAlias

Numeric: TypeAlias = int | float | str | Decimal

MONEY_SCALE = 100
QTY_SCALE = 1000

CENT = Decimal("0.01")
MILLI = Decimal("0.001")
PCT = Decimal("0.01")


class MoneyConversionError(ValueError):
    """Значение невозможно интерпретировать как денежную сумму/количество."""


def _to_decimal(value: Numeric) -> Decimal:
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, bool):
        raise MoneyConversionError(f"Логическое значение не является числом: {value!r}")
    elif isinstance(value, int):
        result = Decimal(value)
    else:
        try:
            # str(float) даёт кратчайшее представление, что убирает шум вида 0.30000000000000004
            result = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise MoneyConversionError(f"Не число: {value!r}") from exc
    if not result.is_finite():
        raise MoneyConversionError(f"Значение не конечно: {value!r}")
    return result


def q2(value: Numeric) -> Decimal:
    """Округление до копеек (half-up — как в бухгалтерских расчётах)."""
    return _to_decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def q3(value: Numeric) -> Decimal:
    return _to_decimal(value).quantize(MILLI, rounding=ROUND_HALF_UP)


def to_kopecks(value: Numeric) -> int:
    return int((_to_decimal(value) * MONEY_SCALE).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def from_kopecks(value: int | None) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return (Decimal(int(value)) / MONEY_SCALE).quantize(CENT)


def to_milli(value: Numeric) -> int:
    return int((_to_decimal(value) * QTY_SCALE).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def from_milli(value: int | None) -> Decimal:
    if value is None:
        return Decimal("0.000")
    return (Decimal(int(value)) / QTY_SCALE).quantize(MILLI)


def apply_ratio(kopecks: int, ratio: Numeric) -> int:
    """Умножение суммы в копейках на коэффициент с округлением до копейки."""
    return int((Decimal(kopecks) * _to_decimal(ratio)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def share_pct(part: int, whole: int) -> Decimal:
    """Доля в процентах с двумя знаками; 0 при нулевом знаменателе."""
    if not whole:
        return Decimal("0.00")
    return (Decimal(part) * 100 / Decimal(whole)).quantize(CENT, rounding=ROUND_HALF_UP)


def as_float(value: Decimal) -> float:
    """Представление для JSON/графиков. Точность уже зафиксирована округлением."""
    return float(value)
