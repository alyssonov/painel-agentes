"""Gráficos da Margem real: faturamento e lucro real por dia (SVG gerado no servidor).

Sem bibliotecas de terceiros (CSP 'self') e sem estilo inline: a geometria é
calculada aqui e o template só desenha. Regras de visualização seguidas:
uma escala por gráfico (faturamento e lucro em gráficos separados), barras
finas (≤ 24px) com ponta arredondada de 4px e base reta, grade discreta,
dica ao passar o mouse e tabela com os mesmos números.
"""

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.money import format_brl
from app.core.timeutil import local_day_bounds_utc, local_today, to_local
from app.models import Order, OrderItem
from app.models.enums import KanbanStatus
from app.services.dashboard import order_margin

WEEKDAYS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


@dataclass
class DayPoint:
    day: date
    revenue: int = 0
    profit: int = 0
    orders: int = 0


def daily_points(db: Session, days: int, tax_rate_pct: float, *, demo: bool = False) -> list[DayPoint]:
    """Um ponto por dia (fuso da loja), incluindo os dias sem venda."""
    today = local_today()
    first = today - timedelta(days=days - 1)
    start, _ = local_day_bounds_utc(first)
    _, end = local_day_bounds_utc(today)
    points = {first + timedelta(days=i): DayPoint(first + timedelta(days=i)) for i in range(days)}
    for order in db.scalars(select(Order).where(
            Order.ordered_at >= start, Order.ordered_at < end, Order.kanban_status != KanbanStatus.CANCELLED,
            Order.is_demo.is_(demo)).options(selectinload(Order.items).selectinload(OrderItem.product))):
        point = points.get(to_local(order.ordered_at).date())
        if point is None:
            continue
        m = order_margin(order, tax_rate_pct)
        point.revenue += m.revenue_cents
        point.profit += m.profit_cents
        point.orders += 1
    return [points[d] for d in sorted(points)]


# --- Geometria ---------------------------------------------------------------------

@dataclass
class Bar:
    path: str
    hit_x: float
    hit_w: float
    negative: bool
    tip: str


@dataclass
class Tick:
    y: float
    label: str


@dataclass
class XLabel:
    x: float
    label: str


@dataclass
class BarChart:
    width: int
    height: int
    plot_left: int
    plot_right: int
    plot_top: int
    plot_bottom: int
    baseline: float
    bars: list[Bar] = field(default_factory=list)
    ticks: list[Tick] = field(default_factory=list)
    xlabels: list[XLabel] = field(default_factory=list)
    has_negative: bool = False


def nice_step(span: float, target_ticks: int = 4) -> float:
    """Passo "redondo" (1, 2, 5 × 10ⁿ) para a grade."""
    if span <= 0:
        return 1.0
    raw = span / target_ticks
    power = 10 ** math.floor(math.log10(raw))
    for mult in (1, 2, 5, 10):
        if raw <= mult * power:
            return mult * power
    return 10 * power


def money_tick(cents: float) -> str:
    """Rótulo do eixo: completo abaixo de R$ 10 mil, compacto acima (um estilo por eixo)."""
    reais = cents / 100
    if abs(reais) >= 10_000:
        return f"R$ {reais / 1000:,.0f} mil".replace(",", ".")
    return format_brl(round(cents)).split(",")[0]


def _bar_path(x: float, w: float, y_value: float, baseline: float, radius: float = 4.0) -> str:
    """Retângulo com a ponta (lado do valor) arredondada e a base reta."""
    h = abs(baseline - y_value)
    r = min(radius, h, w / 2)
    if h <= 0:
        return ""
    if y_value < baseline:  # para cima
        top = y_value
        return (f"M{x:.1f},{baseline:.1f} V{top + r:.1f} Q{x:.1f},{top:.1f} {x + r:.1f},{top:.1f} "
                f"H{x + w - r:.1f} Q{x + w:.1f},{top:.1f} {x + w:.1f},{top + r:.1f} V{baseline:.1f} Z")
    bottom = y_value  # para baixo (valor negativo)
    return (f"M{x:.1f},{baseline:.1f} V{bottom - r:.1f} Q{x:.1f},{bottom:.1f} {x + r:.1f},{bottom:.1f} "
            f"H{x + w - r:.1f} Q{x + w:.1f},{bottom:.1f} {x + w:.1f},{bottom - r:.1f} V{baseline:.1f} Z")


def bar_chart(points: list[DayPoint], metric: str, *, width: int = 760, height: int = 240) -> BarChart:
    values = [getattr(p, metric) for p in points]
    hi, lo = max(values + [0]), min(values + [0])
    step = nice_step(hi - lo or 100_00)
    top_v = math.ceil(hi / step) * step if hi > 0 else step
    bottom_v = math.floor(lo / step) * step if lo < 0 else 0
    left, right, top, bottom = 76, width - 8, 12, height - 28
    span = top_v - bottom_v

    def y_of(v: float) -> float:
        return top + (top_v - v) / span * (bottom - top)

    chart = BarChart(width, height, left, right, top, bottom, y_of(0), has_negative=lo < 0)
    tick = bottom_v
    while tick <= top_v + 1e-6:
        chart.ticks.append(Tick(y_of(tick), money_tick(tick)))
        tick += step
    band = (right - left) / max(len(points), 1)
    bar_w = max(min(24.0, band - 2), 1.0)  # 2px de ar entre barras vizinhas
    every = max(1, math.ceil(len(points) / 8))  # no máximo ~8 datas no eixo
    for i, (point, value) in enumerate(zip(points, values, strict=True)):
        x0 = left + i * band
        x = x0 + (band - bar_w) / 2
        label = f"{WEEKDAYS[point.day.weekday()]} {point.day:%d/%m}"
        tip = (f"{label} · {format_brl(value)}" + (" (prejuízo)" if value < 0 else "")
               + f" · {point.orders} pedido(s)")
        chart.bars.append(Bar(_bar_path(x, bar_w, y_of(value), chart.baseline), x0, band, value < 0, tip))
        if (len(points) - 1 - i) % every == 0:
            chart.xlabels.append(XLabel(x0 + band / 2, f"{point.day:%d/%m}"))
    return chart
