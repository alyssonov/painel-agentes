"""Resumo do mês para a Visão geral: faturamento, lucro real, pedidos, ticket médio e marketplaces.

Comparação justa: o mês atual até agora × o mês passado até o mesmo dia e horário.
Lucro real usa a mesma conta da Margem real (order_margin).
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.timeutil import local_day_bounds_utc, local_today, utcnow
from app.models import Order, OrderItem
from app.models.enums import CHANNEL_LABELS, Channel, KanbanStatus
from app.services.dashboard import money_confirmed, order_margin
from app.services.store_settings import get_setting


@dataclass
class Totals:
    revenue: int = 0
    profit: int = 0
    orders: int = 0
    units: int = 0
    unconfirmed: int = 0  # vendas com taxa/frete ainda não conferidos

    @property
    def ticket(self) -> int | None:
        return self.revenue // self.orders if self.orders else None

    @property
    def margin_pct(self) -> float | None:
        return self.profit / self.revenue * 100 if self.revenue else None


@dataclass
class ChannelTotals:
    channel: str
    label: str
    totals: Totals = field(default_factory=Totals)
    share_pct: float = 0.0


@dataclass
class MonthSummary:
    current: Totals
    previous: Totals
    channels: list[ChannelTotals]
    month_label: str

    @staticmethod
    def delta(now: int, before: int) -> float | None:
        return (now - before) / before * 100 if before else None


MONTHS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
          "novembro", "dezembro"]


def _first_of_previous(day: date) -> date:
    return (day.replace(day=1) - timedelta(days=1)).replace(day=1)


def _totals(db: Session, start, end, tax_rate: float, per_channel: dict | None = None) -> Totals:
    total = Totals()
    for order in db.scalars(select(Order).where(Order.ordered_at >= start, Order.ordered_at < end,
                                                Order.kanban_status != KanbanStatus.CANCELLED)
                            .options(selectinload(Order.items).selectinload(OrderItem.product))):
        m = order_margin(order, tax_rate)
        units = sum(i.quantity for i in order.items)
        for bucket in [total] + ([per_channel.setdefault(order.channel, Totals())] if per_channel is not None else []):
            bucket.revenue += m.revenue_cents
            bucket.profit += m.profit_cents
            bucket.orders += 1
            bucket.units += units
            bucket.unconfirmed += 0 if money_confirmed(order) else 1
    return total


def month_summary(db: Session) -> MonthSummary:
    today = local_today()
    tax_rate = float(get_setting(db, "tax_rate_pct") or 0)
    start, _ = local_day_bounds_utc(today.replace(day=1))
    now = utcnow()
    elapsed = now - start
    prev_first = _first_of_previous(today)
    prev_start, _ = local_day_bounds_utc(prev_first)
    prev_end = min(prev_start + elapsed, start)  # mesmo pedaço do mês passado (sem passar do fim dele)

    per_channel: dict[str, Totals] = {}
    current = _totals(db, start, now, tax_rate, per_channel)
    previous = _totals(db, prev_start, prev_end, tax_rate)
    channels = []
    for key, totals in sorted(per_channel.items(), key=lambda kv: -kv[1].revenue):
        try:
            label = CHANNEL_LABELS[Channel(key)]
        except ValueError:
            label = key
        share = totals.revenue / current.revenue * 100 if current.revenue else 0.0
        channels.append(ChannelTotals(key, label, totals, share))
    return MonthSummary(current, previous, channels, f"{MONTHS[today.month - 1]} de {today.year}")
