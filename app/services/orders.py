"""Central de pedidos: lista com filtros e lista de separação (picking). Só leitura.

A situação de cada pedido combina o Olist (situação do pedido) com o envio do
marketplace (prazo de despacho e se o pacote já saiu), do mesmo jeito que o
quadro "Envios de hoje" da Visão geral.
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.db import utcnow
from app.core.money import format_brl
from app.core.timeutil import local_day_bounds_utc, local_today, to_local
from app.integrations.mercadolivre.mapping import left_the_store
from app.models import Order, OrderItem, Product, Shipment, StockSnapshot
from app.models.enums import CHANNEL_LABELS, Channel, KanbanStatus

PAGE = 50
PICK_NO_DEADLINE_DAYS = 7
PERIODS = {"hoje": "Hoje", "7": "7 dias", "30": "30 dias", "90": "90 dias"}
SITUATIONS = {
    "a_enviar": "A enviar", "atrasado": "Atrasado", "enviado": "Enviado", "entregue": "Entregue",
    "problema": "Com problema", "cancelado": "Cancelado", "full": "Full (envia o Mercado Livre)",
}
# Tom de cada situação (cores de status com significado fixo).
TONES = {"a_enviar": "info", "atrasado": "erro", "enviado": "ok", "entregue": "ok", "problema": "atencao",
         "cancelado": "pausado", "full": "pausado"}


@dataclass
class OrderRow:
    id: int
    channel: str
    channel_label: str
    number: str
    olist_number: str | None
    ordered_at: datetime
    customer: str
    items: list[tuple[int, str, str]]  # (quantidade, título, sku)
    total_cents: int
    situation: str
    situation_label: str
    deadline: datetime | None
    has_invoice: bool
    problem: str | None

    @property
    def tone(self) -> str:
        return TONES[self.situation]


@dataclass
class OrderFilter:
    period: str = "7"
    channel: str = ""
    situation: str = ""
    q: str = ""
    page: int = 1

    @classmethod
    def from_query(cls, periodo: str, canal: str, situacao: str, busca: str, pagina: int) -> "OrderFilter":
        return cls(period=periodo if periodo in PERIODS else "7",
                   channel=canal if canal in {c.value for c in Channel} else "",
                   situation=situacao if situacao in SITUATIONS else "", q=busca.strip()[:80], page=max(1, pagina))


@dataclass
class OrderList:
    rows: list[OrderRow]
    total: int
    revenue_cents: int
    counts: dict[str, int] = field(default_factory=dict)
    pages: int = 1


def situation_of(order: Order, shipment: Shipment | None, now: datetime) -> tuple[str, datetime | None]:
    """Situação do pedido e prazo de despacho (UTC)."""
    if order.kanban_status == KanbanStatus.CANCELLED:
        return "cancelado", None
    deadline = shipment.dispatch_deadline if shipment else None
    if shipment and shipment.logistic_type == "fulfillment":
        return "full", None
    if shipment and shipment.status == "delivered":
        return "entregue", deadline
    if (shipment and left_the_store(shipment.status, shipment.substatus)) or order.kanban_status == KanbanStatus.SHIPPED:
        return "enviado", deadline
    if shipment and shipment.status in ("cancelled", "not_delivered"):
        return "problema", deadline
    if deadline and deadline <= now:
        return "atrasado", deadline
    if order.kanban_status == KanbanStatus.PROBLEM or order.problem_note:
        return "problema", deadline
    return "a_enviar", deadline


def _period_start(period: str) -> datetime:
    today = local_today()
    days = 0 if period == "hoje" else int(period) - 1
    return local_day_bounds_utc(today - timedelta(days=days))[0]


def _shipments_for(db: Session, orders: list[Order]) -> dict[str, Shipment]:
    refs = [o.external_id for o in orders if o.channel == Channel.ML]
    found: dict[str, Shipment] = {}
    for i in range(0, len(refs), 500):
        for s in db.scalars(select(Shipment).where(Shipment.channel == Channel.ML,
                                                   Shipment.order_ref.in_(refs[i:i + 500]))):
            found[s.order_ref] = s
    return found


def list_orders(db: Session, f: OrderFilter, *, paginate: bool = True) -> OrderList:
    stmt = (select(Order).where(Order.ordered_at >= _period_start(f.period))
            .options(selectinload(Order.items)).order_by(Order.ordered_at.desc()))
    if f.channel:
        stmt = stmt.where(Order.channel == f.channel)
    if f.q:
        like = f"%{f.q.lower()}%"
        item_match = select(OrderItem.order_id).where(or_(func.lower(OrderItem.sku).like(like),
                                                          func.lower(OrderItem.title).like(like)))
        stmt = stmt.where(or_(func.lower(Order.external_id).like(like), func.lower(Order.olist_number).like(like),
                              func.lower(Order.customer_short_name).like(like), Order.id.in_(item_match)))
    orders = list(db.scalars(stmt).unique())
    shipments = _shipments_for(db, orders)
    now = utcnow()
    rows, counts = [], {k: 0 for k in SITUATIONS}
    for o in orders:
        situation, deadline = situation_of(o, shipments.get(o.external_id), now)
        counts[situation] += 1
        if f.situation and situation != f.situation:
            continue
        rows.append(OrderRow(
            id=o.id, channel=o.channel, channel_label=CHANNEL_LABELS.get(Channel(o.channel), o.channel),
            number=o.external_id, olist_number=o.olist_number, ordered_at=to_local(o.ordered_at),
            customer=o.customer_short_name, items=[(i.quantity, i.title, i.sku) for i in o.items],
            total_cents=o.items_total_cents, situation=situation, situation_label=SITUATIONS[situation],
            deadline=to_local(deadline) if deadline else None, has_invoice=bool(o.invoice_olist_id),
            problem=o.problem_note))
    revenue = sum(r.total_cents for r in rows if r.situation != "cancelado")
    total = len(rows)
    pages = max(1, -(-total // PAGE))
    if paginate:
        rows = rows[(f.page - 1) * PAGE: f.page * PAGE]
    return OrderList(rows=rows, total=total, revenue_cents=revenue, counts=counts, pages=pages)


def to_csv(result: OrderList) -> str:
    """Planilha (separador ;, padrão do Excel em português). Sem dados pessoais além do nome abreviado."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["Data", "Marketplace", "Pedido", "Pedido Olist", "Cliente", "Itens", "Valor",
                     "Situação", "Prazo de despacho", "Nota fiscal"])
    for r in result.rows:
        writer.writerow([r.ordered_at.strftime("%d/%m/%Y %H:%M"), r.channel_label, r.number, r.olist_number or "",
                         r.customer, " | ".join(f"{q}x {t} ({s})" for q, t, s in r.items),
                         format_brl(r.total_cents), r.situation_label,
                         r.deadline.strftime("%d/%m/%Y %H:%M") if r.deadline else "", "sim" if r.has_invoice else "não"])
    return "﻿" + out.getvalue()  # BOM: o Excel abre os acentos certinho


# --- Lista de separação (picking) ------------------------------------------------

@dataclass
class PickLine:
    sku: str
    title: str
    quantity: int
    orders: list[str]
    stock: int | None
    location: str | None = None

    @property
    def short(self) -> bool:
        return self.stock is not None and self.stock < self.quantity


@dataclass
class PickList:
    lines: list[PickLine]
    orders: int
    units: int
    late: int


def pick_list(db: Session) -> PickList:
    """O que separar agora: pedidos que precisam sair até o fim de hoje (e atrasados), por produto."""
    end = local_day_bounds_utc(local_today())[1]
    since = local_day_bounds_utc(local_today() - timedelta(days=30))[0]
    # Pedido sem prazo do marketplace e "a separar" no Olist há mais de 7 dias costuma ser
    # situação desatualizada: fica fora da lista (continua visível na central de pedidos).
    no_deadline_since = local_day_bounds_utc(local_today() - timedelta(days=PICK_NO_DEADLINE_DAYS))[0]
    orders = list(db.scalars(select(Order).where(Order.ordered_at >= since)
                             .options(selectinload(Order.items))).unique())
    shipments = _shipments_for(db, orders)
    now = utcnow()
    by_sku: dict[str, PickLine] = {}
    n_orders = late = 0
    for o in orders:
        situation, deadline = situation_of(o, shipments.get(o.external_id), now)
        if situation not in ("a_enviar", "atrasado"):
            continue
        # Com prazo do marketplace: só o que vence até o fim de hoje. Sem prazo (Shopee/outros):
        # o que ainda está "a separar"/"embalado" no Olist.
        if deadline and deadline >= end:
            continue
        if not deadline and (o.kanban_status not in (KanbanStatus.TO_PICK, KanbanStatus.PACKED)
                             or o.ordered_at < no_deadline_since):
            continue
        n_orders += 1
        late += situation == "atrasado"
        for item in o.items:
            line = by_sku.setdefault(item.sku, PickLine(item.sku, item.title, 0, [], None))
            line.quantity += item.quantity
            line.orders.append(o.external_id)
    if by_sku:
        products = {p.sku: p for p in db.scalars(select(Product).where(Product.sku.in_(list(by_sku))))}
        latest = select(func.max(StockSnapshot.id)).group_by(StockSnapshot.product_id)
        stock = {s.product_id: s.quantity - (s.reserved or 0)
                 for s in db.scalars(select(StockSnapshot).where(StockSnapshot.id.in_(latest)))}
        for line in by_sku.values():
            product = products.get(line.sku)
            if product:
                line.stock = stock.get(product.id)
                line.location = (product.specs or {}).get("localizacao")
    lines = sorted(by_sku.values(), key=lambda li: (li.location or "~", li.title.lower()))
    return PickList(lines=lines, orders=n_orders, units=sum(li.quantity for li in lines), late=late)
