"""Справочники для генерации: торговые точки и номенклатура.

Значения подобраны так, чтобы порядок величин соответствовал продуктовой рознице
формата «магазин у дома»: это делает результаты расчётов интерпретируемыми, но не
делает данные реальными — см. предупреждение в manifest.json.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StoreDef:
    ref: str
    code: str
    name: str
    city: str
    store_format: str
    weeks: int
    weekly_revenue: int  # рублей в неделю, базовый уровень
    trend_per_week: float
    seasonal_amplitude: float
    noise: float


@dataclass(frozen=True)
class ProductDef:
    ref: str
    sku: str
    name: str
    category: str
    unit: str
    price: float
    cost_ratio: float
    popularity: int


STORES: tuple[StoreDef, ...] = (
    StoreDef(
        "store-01",
        "TT-01",
        "ТТ Центральная",
        "Казань",
        "супермаркет",
        78,
        420_000,
        0.0035,
        0.09,
        0.06,
    ),
    StoreDef(
        "store-02", "TT-02", "ТТ Заречная", "Казань", "у дома", 74, 310_000, 0.0020, 0.07, 0.07
    ),
    StoreDef(
        "store-03",
        "TT-03",
        "ТТ Южная",
        "Набережные Челны",
        "у дома",
        66,
        280_000,
        -0.0015,
        0.11,
        0.08,
    ),
    StoreDef(
        "store-04", "TT-04", "ТТ Новая", "Альметьевск", "у дома", 9, 190_000, 0.0090, 0.05, 0.09
    ),
    StoreDef(
        "store-05", "TT-05", "ТТ Северная", "Казань", "супермаркет", 70, 350_000, 0.0025, 0.08, 0.05
    ),
    StoreDef(
        "store-06", "TT-06", "ТТ Привокзальная", "Казань", "киоск", 58, 145_000, 0.0010, 0.14, 0.11
    ),
)

PRODUCTS: tuple[ProductDef, ...] = (
    ProductDef("nomen-01", "MLK-1L", "Молоко 3,2% 1 л", "Молочная продукция", "шт", 89.0, 0.72, 10),
    ProductDef("nomen-02", "BRD-500", "Хлеб белый 500 г", "Хлеб", "шт", 45.0, 0.55, 12),
    ProductDef("nomen-03", "WTR-05", "Вода питьевая 0,5 л", "Напитки", "шт", 35.0, 0.40, 9),
    ProductDef(
        "nomen-04", "CHS-200", "Сыр полутвёрдый 200 г", "Молочная продукция", "шт", 210.0, 0.68, 5
    ),
    ProductDef("nomen-05", "APL-1KG", "Яблоки, кг", "Овощи и фрукты", "кг", 120.0, 0.60, 7),
    ProductDef("nomen-06", "TEA-25", "Чай чёрный 25 пак.", "Бакалея", "шт", 150.0, 0.50, 4),
    ProductDef(
        "nomen-07", "CHK-1KG", "Курица охлаждённая, кг", "Мясо и птица", "кг", 260.0, 0.74, 6
    ),
    ProductDef("nomen-08", "PST-450", "Макароны 450 г", "Бакалея", "шт", 78.0, 0.52, 6),
    ProductDef("nomen-09", "JUC-1L", "Сок яблочный 1 л", "Напитки", "шт", 125.0, 0.46, 5),
    ProductDef("nomen-10", "EGG-10", "Яйцо куриное С1, 10 шт", "Яйцо", "уп", 115.0, 0.66, 8),
)

COST_RATIO_BY_CATEGORY: dict[str, float] = {
    "Молочная продукция": 0.70,
    "Хлеб": 0.55,
    "Напитки": 0.43,
    "Овощи и фрукты": 0.60,
    "Бакалея": 0.51,
    "Мясо и птица": 0.74,
    "Яйцо": 0.66,
    "Прочее": 0.65,
}

OVERHEAD_RATE = 0.12
