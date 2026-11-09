"""Situação do estoque por produto: saldo no Olist, ritmo de vendas e anúncios.

O estoque vem SEMPRE do Olist (fonte da verdade). Os anúncios servem só para
conferir se estão mostrando o mesmo número.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models import Listing, Order, OrderItem, Product, StockSnapshot
from app.models.enums import CHANNEL_LABELS, Channel, KanbanStatus


@dataclass
class ListingView:
    channel_label: str
    title: str
    quantity: int
    fulfillment: bool
    permalink: str | None
    diverges: bool


@dataclass
class StockRow:
    product_id: int
    sku: str
    name: str
    available: int | None  # saldo − reservado no Olist (None = sem leitura)
    sold_units: int  # vendidos na janela
    avg_daily: float
    days_left: float | None
    status: str  # esgotado | baixo | ok | parado | sem_info
    listings: list[ListingView] = field(default_factory=list)

    @property
    def diverges(self) -> bool:
        return any(listing.diverges for listing in self.listings)


STATUS_ORDER = {"esgotado": 0, "baixo": 1, "sem_info": 2, "ok": 3, "parado": 4}


def stock_rows(db: Session, *, window_days: int = 30, alert_days: int = 7, tolerance: int = 0,
               demo: bool = False) -> list[StockRow]:
    since = utcnow() - timedelta(days=window_days)
    latest = select(func.max(StockSnapshot.id)).group_by(StockSnapshot.product_id)
    snaps = {s.product_id: s for s in db.scalars(select(StockSnapshot).where(StockSnapshot.id.in_(latest)))}
    sold = dict(db.execute(
        select(OrderItem.product_id, func.sum(OrderItem.quantity))
        .join(Order, Order.id == OrderItem.order_id)
        .where(Order.ordered_at >= since, Order.kanban_status != KanbanStatus.CANCELLED,
               Order.is_demo.is_(demo), OrderItem.product_id.is_not(None))
        .group_by(OrderItem.product_id)).all())
    listings: dict[int, list[Listing]] = {}
    for listing in db.scalars(select(Listing).where(Listing.status == "active", Listing.product_id.is_not(None))):
        listings.setdefault(listing.product_id, []).append(listing)

    rows = []
    for product in db.scalars(select(Product).where(Product.is_active.is_(True), Product.is_demo.is_(demo))):
        snap = snaps.get(product.id)
        available = (snap.quantity - snap.reserved) if snap else None
        units = int(sold.get(product.id) or 0)
        avg = units / window_days if window_days else 0.0
        days_left = (available / avg) if (available is not None and avg > 0) else None
        if available is None:
            status = "sem_info"
        elif available <= 0:
            status = "esgotado" if units else "parado"
        elif days_left is not None and days_left <= alert_days:
            status = "baixo"
        elif units == 0:
            status = "parado"
        else:
            status = "ok"
        views = []
        for listing in listings.get(product.id, []):
            diverges = (available is not None and not listing.fulfillment
                        and abs(listing.available_quantity - max(available, 0)) > tolerance)
            label = CHANNEL_LABELS.get(Channel(listing.channel), listing.channel)
            title = listing.title + (f" ({listing.variation_label})" if listing.variation_label else "")
            views.append(ListingView(label, title, listing.available_quantity, listing.fulfillment,
                                     listing.permalink, diverges))
        rows.append(StockRow(product.id, product.sku, product.name, available, units, avg, days_left, status, views))
    rows.sort(key=lambda r: (STATUS_ORDER[r.status], r.days_left if r.days_left is not None else 1e9, r.name))
    return rows
