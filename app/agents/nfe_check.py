"""Agente 9 — Conferência de NF-e (SOMENTE LEITURA: nunca emite, cancela ou altera notas).

- Pedido "Pronto para envio" sem nota autorizada → urgente.
- Pedido aprovado/em preparação há mais de X horas sem nota → atenção.
- Nota rejeitada ou denegada nos últimos dias → urgente.
A correção é sempre feita no Olist.
"""

from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.base import run_agent
from app.agents.config import agent_rules, has_real_data
from app.core.alerts import AlertSpec, sync_alerts
from app.core.db import utcnow
from app.integrations.olist.mapping import ORDER_STATUS_LABELS
from app.models import Invoice, Order
from app.models.enums import CHANNEL_LABELS, AlertSeverity, Channel

SLUG = "nfe"
LOOKBACK_DAYS = 15
# Situações da nota que contam como "tem nota" (API v3: 2 Emitida, 6 Autorizada, 7 Emitida DANFE, 8 Registrada).
OK_INVOICE = {2, 6, 7, 8}
SENDING_INVOICE = {1, 4, 9}  # pendente / enviada aguardando retorno da SEFAZ
BAD_INVOICE = {5: "rejeitada", 10: "denegada"}

READY = ORDER_STATUS_LABELS[7]  # "Pronto para envio"
WAITING = {ORDER_STATUS_LABELS[3], ORDER_STATUS_LABELS[4]}  # Aprovado, Preparando envio


def _order_label(order: Order) -> str:
    channel = CHANNEL_LABELS.get(Channel(order.channel), order.channel)
    return f"pedido {order.olist_number or order.external_id} ({channel})"


def invoice_state(db: Session, order: Order) -> str:
    """'ok', 'enviando', 'problema' ou 'sem_nota' para o pedido."""
    invoices = list(db.scalars(select(Invoice).where(or_(
        Invoice.order_id == order.id,
        Invoice.olist_id == order.invoice_olist_id if order.invoice_olist_id else False))))
    codes = {i.status_code for i in invoices}
    if codes & OK_INVOICE:
        return "ok"
    if codes & SENDING_INVOICE:
        return "enviando"
    if codes & set(BAD_INVOICE):
        return "problema"
    if order.invoice_olist_id and not invoices:
        return "ok"  # o Olist já vinculou uma nota (fora da janela lida): consideramos emitida
    return "sem_nota"


def build_alerts(db: Session, hours_without_invoice: float) -> tuple[list[AlertSpec], int, int]:
    now = utcnow()
    specs: list[AlertSpec] = []
    missing = 0
    orders = db.scalars(select(Order).where(Order.is_demo.is_(False),
                                            Order.ordered_at >= now - timedelta(days=LOOKBACK_DAYS),
                                            Order.olist_status.in_([READY, *WAITING])))
    for order in orders:
        state = invoice_state(db, order)
        if state in ("ok", "enviando"):
            continue
        if order.olist_status == READY:
            missing += 1
            specs.append(AlertSpec(f"pedido:{order.id}", AlertSeverity.CRITICAL,
                                   f"{_order_label(order).capitalize()} está pronto para envio sem nota fiscal",
                                   "Emita a nota no Olist para o pedido não atrasar.", "pedido"))
        elif state == "sem_nota" and now - order.ordered_at > timedelta(hours=hours_without_invoice):
            missing += 1
            hours = int((now - order.ordered_at).total_seconds() // 3600)
            specs.append(AlertSpec(f"pedido:{order.id}", AlertSeverity.WARNING,
                                   f"{_order_label(order).capitalize()} parado há {hours} h sem nota fiscal",
                                   f"Situação no Olist: {order.olist_status}.", "pedido"))
    rejected = 0
    for invoice in db.scalars(select(Invoice).where(Invoice.status_code.in_(list(BAD_INVOICE)),
                                                    Invoice.issued_at >= now - timedelta(days=7))):
        rejected += 1
        word = BAD_INVOICE[invoice.status_code]
        order = db.get(Order, invoice.order_id) if invoice.order_id else None
        specs.append(AlertSpec(f"nota:{invoice.id}", AlertSeverity.CRITICAL,
                               f"Nota fiscal {invoice.number or ''} {word}".replace("  ", " ")
                               + (f" — {_order_label(order)}" if order else ""),
                               "Corrija e reenvie a nota no Olist.", "nota"))
    return specs, missing, rejected


def run_nfe_check(session_factory: sessionmaker[Session]):
    with session_factory() as db:
        if not has_real_data(db):
            return None

        def work(db: Session, agent) -> tuple[int, str]:
            specs, missing, rejected = build_alerts(db, agent_rules(agent)["horas_sem_nota"])
            sync_alerts(db, SLUG, specs)
            db.commit()
            if not missing and not rejected:
                return 0, "Todos os pedidos com nota"
            parts = []
            if missing:
                parts.append(f"{missing} pedido(s) sem nota")
            if rejected:
                parts.append(f"{rejected} nota(s) rejeitada(s)")
            return missing + rejected, " · ".join(parts)

        return run_agent(db, SLUG, work)
