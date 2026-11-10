"""Produtos parados: estoque que não vende (dinheiro empatado).

Sem IA (custo zero). Dois casos:
  "parado"  → tem estoque e não vende há N dias (ou nunca vendeu no histórico)
  "excesso" → vende, mas o estoque dura mais de M dias no ritmo atual
Dinheiro parado = disponível no Olist × custo do produto no Olist.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models import Order, OrderItem, Product
from app.models.enums import KanbanStatus
from app.services.stock import StockRow

IDLE_LABELS = {"parado": ("Parado", "atencao"), "excesso": ("Estoque demais", "info")}


@dataclass
class IdleItem:
    sku: str
    name: str
    available: int
    unit_cost_cents: int
    last_sale: datetime | None
    days_left: float | None
    kind: str  # parado | excesso

    @property
    def tied_cents(self) -> int:
        return self.available * self.unit_cost_cents


@dataclass
class IdleReport:
    items: list[IdleItem]
    history_since: datetime | None  # venda mais antiga que o painel conhece
    history_complete: bool  # o histórico cobre o período pedido?

    @property
    def tied_cents(self) -> int:
        return sum(i.tied_cents for i in self.items)


def idle_products(db: Session, rows: list[StockRow], *, idle_days: int, excess_days: int,
                  demo: bool = False) -> IdleReport:
    now = utcnow()
    valid = (Order.kanban_status != KanbanStatus.CANCELLED, Order.is_demo.is_(demo))
    last_sales = dict(db.execute(
        select(OrderItem.product_id, func.max(Order.ordered_at)).join(Order, Order.id == OrderItem.order_id)
        .where(*valid, OrderItem.product_id.is_not(None)).group_by(OrderItem.product_id)).all())
    history_since = db.scalar(select(func.min(Order.ordered_at)).where(*valid))
    ids = [r.product_id for r in rows]
    costs = dict(db.execute(select(Product.id, Product.cost_cents).where(Product.id.in_(ids))).all()) if ids else {}
    limit = now - timedelta(days=idle_days)

    items = []
    for row in rows:
        if row.available is None or row.available <= 0:
            continue
        last = last_sales.get(row.product_id)
        if last is None or last < limit:
            kind = "parado"
        elif row.days_left is not None and row.days_left > excess_days:
            kind = "excesso"
        else:
            continue
        items.append(IdleItem(row.sku, row.name, row.available, int(costs.get(row.product_id) or 0), last,
                              row.days_left, kind))
    items.sort(key=lambda i: (-i.tied_cents, i.name))
    complete = history_since is not None and history_since <= limit
    return IdleReport(items, history_since, complete)
