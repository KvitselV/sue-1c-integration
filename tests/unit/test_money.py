"""Точность денежной арифметики — основа доверия к показателям."""

from __future__ import annotations

from decimal import Decimal

import pytest

from sue.money import (
    MoneyConversionError,
    apply_ratio,
    from_kopecks,
    from_milli,
    q2,
    share_pct,
    to_kopecks,
    to_milli,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0),
        (1, 100),
        (4.35, 435),
        ("4.35", 435),
        (Decimal("4.35"), 435),
        (0.1 + 0.2, 30),
        (1234567.89, 123456789),
        (-15.5, -1550),
        (2.005, 201),  # округление half-up, а не банковское
    ],
)
def test_to_kopecks_is_exact(value: object, expected: int) -> None:
    assert to_kopecks(value) == expected


def test_float_noise_does_not_accumulate() -> None:
    """Сумма 0.1 сто раз в float даёт 9.99…; в копейках — ровно 1000."""
    total = sum(to_kopecks(0.1) for _ in range(100))
    assert total == 1000
    assert from_kopecks(total) == Decimal("10.00")


def test_roundtrip_preserves_value() -> None:
    for kopecks in (0, 1, 99, 100, 123456789):
        assert to_kopecks(from_kopecks(kopecks)) == kopecks


def test_quantity_scale_is_three_digits() -> None:
    assert to_milli("1.234") == 1234
    assert from_milli(1234) == Decimal("1.234")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "abc", None, True])
def test_invalid_values_are_rejected(value: object) -> None:
    with pytest.raises(MoneyConversionError):
        to_kopecks(value)


def test_apply_ratio_rounds_to_kopeck() -> None:
    # 1000.01 руб. × 0.65 = 650.0065 руб. → 650.01 руб.
    assert apply_ratio(100_001, Decimal("0.65")) == 65_001


def test_share_pct_handles_zero_denominator() -> None:
    assert share_pct(0, 0) == Decimal("0.00")
    assert share_pct(1, 3) == Decimal("33.33")


def test_q2_uses_half_up() -> None:
    assert q2("2.345") == Decimal("2.35")
    assert q2("-2.345") == Decimal("-2.35")
