"""Previsão de recebimentos do Mercado Livre: quando o dinheiro de cada venda é liberado.

Só LEITURA. Fontes (documentação oficial):
- Vendas: GET /orders/search (já lido pela sincronização) traz os pagamentos
  (id, status, valor, data de aprovação), mas NÃO a data de liberação.
- Mercado Pago: GET /v1/payments/{id} traz money_release_date,
  money_release_status e transaction_details.net_received_amount.
  https://www.mercadopago.com.br/developers/pt/reference/online-payments/checkout-pro/get-payment/get.md
  (consultado em 11/11/2026; formato confirmado com dados reais da loja no mesmo dia).

O dinheiro costuma ser liberado ~4 semanas depois da venda; por isso, na
primeira vez, lemos os pagamentos das vendas dos últimos 35 dias.
"""

import logging
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.integrations.errors import AuthExpired, IntegrationError, TemporaryError
from app.integrations.mercadolivre.client import MLClient
from app.integrations.mercadopago.client import MercadoPagoClient
from app.integrations.olist.dates import parse_olist_datetime as parse_datetime
from app.integrations.olist.dates import to_cents
from app.models import Payment
from app.models.enums import Channel
from app.sync.common import get_cursor, set_cursor

logger = logging.getLogger("sync.ml.recebimentos")

FIRST_DAYS = 35
MAX_DETAILS_PER_RUN = 120
RECHECK_EVERY = timedelta(hours=6)  # a data pode mudar (atraso na entrega, reclamação...)
RECHECK_NEAR = timedelta(hours=1)  # perto da liberação, confere mais vezes
NEAR_WINDOW = timedelta(days=2)


def record_order_payments(db: Session, order: dict, order_ref: str) -> None:
    """Guarda os pagamentos que vêm junto com a venda (sem consulta extra)."""
    for pay in order.get("payments") or []:
        payment_id = str(pay.get("id") or "")
        if not payment_id:
            continue
        row = db.scalar(select(Payment).where(Payment.channel == Channel.ML, Payment.payment_id == payment_id))
        if row is None:
            row = Payment(channel=Channel.ML, payment_id=payment_id, order_ref=order_ref, status="desconhecido")
            db.add(row)
        status = pay.get("status") or row.status
        if status != row.status:
            row.checked_at = None  # mudou (ex.: estorno): confere de novo no Mercado Pago
        row.status = status
        row.amount_cents = to_cents(pay.get("total_paid_amount") or pay.get("transaction_amount")) or row.amount_cents
        row.approved_at = parse_datetime(pay.get("date_approved")) or row.approved_at


def _needs_check(now):
    return or_(
        Payment.checked_at.is_(None),
        Payment.checked_at < now - RECHECK_EVERY,
        (Payment.release_date < now + NEAR_WINDOW) & (Payment.checked_at < now - RECHECK_NEAR),
    )


def sync_payments(db: Session, client: MLClient, mp: MercadoPagoClient, account, seller_id: str, started) -> int:
    """Completa data de liberação e valor líquido pelo Mercado Pago (aos poucos)."""
    if get_cursor(account, "cursor_pagamentos_inicio") is None:
        for order in client.iter_orders(seller_id, created_from=started - timedelta(days=FIRST_DAYS)):
            record_order_payments(db, order, str(order.get("pack_id") or order.get("id")))
        set_cursor(account, "cursor_pagamentos_inicio", started)
        db.commit()

    now = utcnow()
    pending = list(db.scalars(select(Payment).where(
        Payment.channel == Channel.ML, Payment.is_demo.is_(False), Payment.status == "approved",
        or_(Payment.release_status.is_(None), Payment.release_status != "released"), _needs_check(now))
        .order_by(Payment.checked_at.is_not(None), Payment.checked_at).limit(MAX_DETAILS_PER_RUN)))
    checked = 0
    for row in pending:
        try:
            data = mp.get_payment(row.payment_id)
        except AuthExpired as exc:
            # Recusa do Mercado Pago não pode desconectar o Mercado Livre: só registra e para.
            logger.warning("Mercado Pago recusou o acesso aos pagamentos: %s", exc)
            break
        except TemporaryError as exc:
            logger.warning("Mercado Pago pediu pausa (%s). Continua na próxima rodada.", exc)
            break
        except IntegrationError as exc:
            logger.warning("Pagamento %s não pôde ser lido: %s", row.payment_id, exc)
            row.checked_at = now
            continue
        if data:
            row.status = data.get("status") or row.status
            row.release_date = parse_datetime(data.get("money_release_date")) or row.release_date
            row.release_status = data.get("money_release_status") or row.release_status
            net = (data.get("transaction_details") or {}).get("net_received_amount")
            row.net_cents = to_cents(net) if net is not None else row.net_cents
        row.checked_at = now
        checked += 1
        if checked % 20 == 0:
            db.commit()
    link_net_received(db)
    db.commit()
    return checked


LINK_WINDOW = timedelta(days=60)


def link_net_received(db: Session) -> int:
    """Grava em cada venda do Mercado Livre o valor que a loja recebe (soma dos pagamentos aprovados).

    É o mesmo número do "detalhe da venda" no Mercado Livre: preço − taxa − frete − tarifas.
    Só grava quando TODOS os pagamentos aprovados da venda já têm o valor líquido; senão a
    venda fica "ainda não conferida" (a margem avisa, em vez de mostrar lucro inflado).
    """
    from app.models import Order  # import local: evita ciclo models ↔ sync

    since = utcnow() - LINK_WINDOW
    totals: dict[str, int] = {}
    incomplete: set[str] = set()
    for row in db.scalars(select(Payment).where(Payment.channel == Channel.ML, Payment.status == "approved",
                                                or_(Payment.approved_at.is_(None), Payment.approved_at >= since))):
        if row.net_cents is None:
            incomplete.add(row.order_ref)
        else:
            totals[row.order_ref] = totals.get(row.order_ref, 0) + row.net_cents
    changed = 0
    for order in db.scalars(select(Order).where(Order.channel == Channel.ML, Order.ordered_at >= since)):
        value = None if order.external_id in incomplete else totals.get(order.external_id)
        if value is not None and order.net_received_cents != value:
            order.net_received_cents = value
            changed += 1
    return changed
