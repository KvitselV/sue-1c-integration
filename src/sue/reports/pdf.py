"""PDF-отчёты по торговым точкам.

Шрифт DejaVu Sans лежит в пакете: кириллица должна читаться без системных шрифтов
и без обращения в интернет. Это не промышленный конструктор отчётов — один лист
с теми же показателями, что на карточке точки.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fpdf import FPDF

from sue import __version__
from sue.domain.mart import margin_status
from sue.i18n import ru_margin_status, ru_source

_FONTS = Path(__file__).resolve().parents[1] / "web" / "static" / "fonts"
_DISCLAIMER = (
    "Данные синтетические и структурно совместимы с контрактом 1С:Розница. "
    "Это не выгрузка действующей информационной базы. "
    "Метка «модель» означает расчётную величину, а не учёт."
)


def _money(value: Any) -> str:
    if value is None:
        return "—"
    number = float(value)
    text = f"{number:,.2f}"
    return text.replace(",", " ").replace(".", ",")


def _pct(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value):.1f}".replace(".", ",") + " %"


def _plain(value: Any) -> Any:
    return value["value"] if isinstance(value, dict) else value


def _source(value: Any) -> str:
    if isinstance(value, dict) and value.get("source"):
        return ru_source(str(value["source"]))
    return "—"


class _Report(FPDF):
    def __init__(self, subtitle: str) -> None:
        super().__init__(format="A4", unit="mm")
        self.subtitle = subtitle
        self.set_auto_page_break(auto=True, margin=18)
        self.add_font("DejaVu", "", str(_FONTS / "DejaVuSans.ttf"))
        self.add_font("DejaVu", "B", str(_FONTS / "DejaVuSans-Bold.ttf"))

    def header(self) -> None:
        self.set_font("DejaVu", "B", 11)
        self.set_text_color(15, 107, 76)
        self.cell(0, 6, "СУЭ", new_x="LMARGIN", new_y="NEXT")
        self.set_font("DejaVu", "", 9)
        self.set_text_color(90, 102, 94)
        self.cell(0, 5, self.subtitle, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(15, 107, 76)
        self.set_line_width(0.4)
        self.line(self.l_margin, self.get_y() + 1, self.w - self.r_margin, self.get_y() + 1)
        self.ln(6)

    def footer(self) -> None:
        self.set_y(-16)
        self.set_font("DejaVu", "", 7)
        self.set_text_color(110, 120, 112)
        self.multi_cell(0, 3.4, _DISCLAIMER)
        self.cell(0, 4, f"Стр. {self.page_no()}", align="R")


def _heading(pdf: _Report, text: str) -> None:
    pdf.set_font("DejaVu", "B", 12)
    pdf.set_text_color(24, 35, 28)
    pdf.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)


def _kv_table(pdf: _Report, rows: list[tuple[str, str, str]]) -> None:
    usable = pdf.w - pdf.l_margin - pdf.r_margin
    widths = (usable * 0.42, usable * 0.38, usable * 0.20)
    pdf.set_font("DejaVu", "B", 8)
    pdf.set_fill_color(238, 243, 239)
    pdf.set_text_color(90, 102, 94)
    for title, width in zip(("Показатель", "Значение", "Источник"), widths, strict=True):
        pdf.cell(width, 7, title, border=0, fill=True)
    pdf.ln()
    pdf.set_font("DejaVu", "", 9)
    pdf.set_text_color(24, 35, 28)
    fill = False
    for name, value, source in rows:
        pdf.set_fill_color(248, 250, 247) if fill else pdf.set_fill_color(255, 255, 255)
        pdf.cell(widths[0], 7, name, fill=True)
        pdf.cell(widths[1], 7, value, fill=True, align="R")
        pdf.cell(widths[2], 7, source, fill=True, align="R")
        pdf.ln()
        fill = not fill
    pdf.ln(3)


def render_store_report(
    item: dict[str, Any],
    categories: list[dict[str, Any]],
    *,
    generated_at: datetime | None = None,
    version: str = __version__,
) -> bytes:
    """Одностраничный отчёт по торговой точке."""
    stamp = generated_at or datetime.now(UTC)
    title = f"{item['store_code']} · {item['store_name']}"
    pdf = _Report("Отчёт по торговой точке")
    pdf.add_page()
    _heading(pdf, title)
    pdf.set_font("DejaVu", "", 9)
    pdf.set_text_color(90, 102, 94)
    city = item.get("city") or "—"
    pdf.cell(
        0,
        6,
        f"{city} · период {item['period_from']} — {item['period_to']}",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.cell(
        0,
        6,
        f"Сформирован {stamp.strftime('%d.%m.%Y %H:%M')} UTC · версия {version}",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(2)

    metrics = [
        ("Выручка", _money(_plain(item["revenue"])), _source(item["revenue"])),
        ("Возвраты", _money(_plain(item["returns"])), _source(item["returns"])),
        ("Себестоимость", _money(_plain(item["cost"])), _source(item["cost"])),
        ("Валовая прибыль", _money(_plain(item["gross_profit"])), _source(item["gross_profit"])),
        (
            "Валовая рентабельность",
            _pct(_plain(item["gross_margin_pct"])),
            _source(item["gross_margin_pct"]),
        ),
        ("Накладные", _money(_plain(item["overhead"])), _source(item["overhead"])),
        (
            "Операционная прибыль",
            _money(_plain(item["operating_profit"])),
            _source(item["operating_profit"]),
        ),
        (
            "Операционная рентабельность",
            _pct(_plain(item["operating_margin_pct"])),
            _source(item["operating_margin_pct"]),
        ),
    ]
    _kv_table(pdf, metrics)

    pdf.set_font("DejaVu", "", 9)
    pdf.cell(
        0,
        6,
        (
            f"Себестоимость: учёт {_pct(item['cost_accounting_share_pct'])} / "
            f"модель {_pct(item['cost_modeled_share_pct'])} · "
            f"строк без себестоимости {item['lines_without_cost']} из {item['lines']}"
        ),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(3)

    if categories:
        _heading(pdf, "Категории номенклатуры")
        usable = pdf.w - pdf.l_margin - pdf.r_margin
        widths = (usable * 0.36, usable * 0.16, usable * 0.16, usable * 0.16, usable * 0.16)
        headers = ("Категория", "Выручка", "Себест.", "Вал. пр.", "Вал. %")
        pdf.set_font("DejaVu", "B", 8)
        pdf.set_fill_color(238, 243, 239)
        pdf.set_text_color(90, 102, 94)
        for header, width in zip(headers, widths, strict=True):
            pdf.cell(width, 7, header, fill=True)
        pdf.ln()
        pdf.set_font("DejaVu", "", 8)
        pdf.set_text_color(24, 35, 28)
        for row in categories:
            category = str(row["category"])
            if len(category) > 24:
                category = category[:23] + "…"
            pdf.cell(widths[0], 6, category)
            pdf.cell(widths[1], 6, _money(row["revenue"]), align="R")
            pdf.cell(widths[2], 6, _money(row["cost"]), align="R")
            pdf.cell(widths[3], 6, _money(row["gross_profit"]), align="R")
            pdf.cell(widths[4], 6, _pct(row["gross_margin_pct"]), align="R")
            pdf.ln()

    return bytes(pdf.output())


def render_network_report(
    rows: list[dict[str, Any]],
    *,
    period_from: str | None,
    period_to: str | None,
    generated_at: datetime | None = None,
    version: str = __version__,
) -> bytes:
    """Сводный отчёт по всем торговым точкам за период."""
    stamp = generated_at or datetime.now(UTC)
    period = "весь загруженный период"
    if period_from or period_to:
        period = f"{period_from or '…'} — {period_to or '…'}"
    pdf = _Report("Отчёт по торговым точкам")
    pdf.add_page()
    _heading(pdf, "Рентабельность сети")
    pdf.set_font("DejaVu", "", 9)
    pdf.set_text_color(90, 102, 94)
    pdf.cell(0, 6, f"Период: {period}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(
        0,
        6,
        f"Сформирован {stamp.strftime('%d.%m.%Y %H:%M')} UTC · {version} · точек {len(rows)}",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(3)

    usable = pdf.w - pdf.l_margin - pdf.r_margin
    widths = (
        usable * 0.22,
        usable * 0.18,
        usable * 0.18,
        usable * 0.14,
        usable * 0.14,
        usable * 0.14,
    )
    headers = ("ТТ", "Выручка", "Себест.", "Вал. %", "Опер. %", "Оценка")
    pdf.set_font("DejaVu", "B", 8)
    pdf.set_fill_color(238, 243, 239)
    pdf.set_text_color(90, 102, 94)
    for header, width in zip(headers, widths, strict=True):
        pdf.cell(width, 7, header, fill=True)
    pdf.ln()
    pdf.set_font("DejaVu", "", 8)
    pdf.set_text_color(24, 35, 28)
    for row in rows:
        name = f"{row['store_code']} {row['store_name']}"
        if len(name) > 28:
            name = name[:27] + "…"
        pdf.cell(widths[0], 6, name)
        pdf.cell(widths[1], 6, _money(_plain(row["revenue"])), align="R")
        pdf.cell(widths[2], 6, _money(_plain(row["cost"])), align="R")
        pdf.cell(widths[3], 6, _pct(_plain(row["gross_margin_pct"])), align="R")
        pdf.cell(widths[4], 6, _pct(_plain(row["operating_margin_pct"])), align="R")
        status = ru_margin_status(margin_status(float(_plain(row["operating_margin_pct"]))))
        pdf.cell(widths[5], 6, status, align="R")
        pdf.ln()
    return bytes(pdf.output())
