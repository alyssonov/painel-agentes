"""Relatório de margem real por período: totais, por produto e vendas no prejuízo.

Fórmula (app/services/margin.py): preço − taxa do marketplace − frete pago pela
loja − custo do produto (Olist) − impostos (alíquota da loja).
Taxa, frete e imposto do pedido são divididos entre os itens na proporção do
valor de cada item.
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Order, OrderItem
from app.models.enums import CHANNEL_LABELS, Channel, KanbanStatus
from app.services.dashboard import money_confirmed, order_margin


@dataclass
class Totals:
    revenue: int = 0
    fees: int = 0
    shipping: int = 0
    cost: int = 0
    tax: int = 0
    profit: int = 0
    orders: int = 0

    @property
    def margin_pct(self) -> float | None:
        return self.profit / self.revenue * 100 if self.revenue else None


@dataclass
class ProductMargin:
    key: str
    name: str
    sku: str
    units: int = 0
    revenue: int = 0
    profit: int = 0
    missing_cost: bool = False
    missing_fees: bool = False

    @property
    def margin_pct(self) -> float | None:
        return self.profit / self.revenue * 100 if self.revenue else None


@dataclass
class LossOrder:
    order_id: int
    label: str
    channel_label: str
    ordered_at: datetime
    revenue: int
    profit: int
    margin_pct: float | None


@dataclass
class MarginReport:
    totals: Totals
    products: list[ProductMargin] = field(default_factory=list)
    loss_orders: list[LossOrder] = field(default_factory=list)
    orders_missing_fees: int = 0
    products_missing_cost: int = 0


def margin_report(db: Session, start: datetime, end: datetime, tax_rate_pct: float, *,
                  demo: bool = False) -> MarginReport:
    orders = db.scalars(
        select(Order).where(Order.ordered_at >= start, Order.ordered_at < end,
                            Order.kanban_status != KanbanStatus.CANCELLED, Order.is_demo.is_(demo))
        .options(selectinload(Order.items).selectinload(OrderItem.product)))
    totals = Totals()
    products: dict[str, ProductMargin] = {}
    losses: list[LossOrder] = []
    missing_fees = 0
    for order in orders:
        m = order_margin(order, tax_rate_pct)
        totals.revenue += m.revenue_cents
        totals.fees += m.fee_cents
        totals.shipping += m.shipping_cents
        totals.cost += m.cost_cents
        totals.tax += m.tax_cents
        totals.profit += m.profit_cents
        totals.orders += 1
        if not money_confirmed(order):
            missing_fees += 1
        if m.is_loss:
            losses.append(LossOrder(order.id, order.external_id,
                                    CHANNEL_LABELS.get(Channel(order.channel), order.channel),
                                    order.ordered_at, m.revenue_cents, m.profit_cents, m.margin_pct))

        items_total = sum(i.quantity * i.unit_price_cents for i in order.items) or 1
        for item in order.items:
            item_rev = item.quantity * item.unit_price_cents
            share = item_rev / items_total
            cost = item.quantity * (item.product.cost_cents if item.product else 0)
            profit = round(item_rev - (m.fee_cents + m.shipping_cents) * share - item_rev * tax_rate_pct / 100) - cost
            key = str(item.product_id or item.sku)
            entry = products.get(key)
            if entry is None:
                name = item.product.name if item.product else item.title
                entry = products[key] = ProductMargin(key, name, item.product.sku if item.product else item.sku)
            entry.units += item.quantity
            entry.revenue += item_rev
            entry.profit += profit
            entry.missing_cost = entry.missing_cost or not (item.product and item.product.cost_cents > 0)
            entry.missing_fees = entry.missing_fees or not money_confirmed(order)

    product_list = sorted(products.values(), key=lambda p: (p.profit >= 0, p.margin_pct if p.margin_pct is not None else 0))
    losses.sort(key=lambda o: o.profit)
    return MarginReport(totals, product_list, losses, missing_fees,
                        sum(1 for p in product_list if p.missing_cost))
