"""Shopee → banco local (somente leitura).

- Mantém a autorização viva: o access_token vale 4 horas e o refresh_token 30 dias
  (uso único); renovar com frequência evita "Conectar de novo".
- Lê os pedidos alterados (situação e prazo de despacho) e as taxas de cada venda
  (`run_shopee_sync`). Os pedidos em si vêm do Olist; ver o docstring da função.
"""

import logging
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import utcnow
from app.integrations.errors import AuthExpired, IntegrationError, NotConnected, TemporaryError
from app.integrations.shopee.auth import ShopeeTokenProvider, get_shopee_account
from app.integrations.shopee.client import ShopeeClient, from_timestamp
from app.integrations.shopee.mapping import DETAIL_FIELDS, NO_ESCROW, income_from, shipment_from
from app.models import MarketplaceFee, Order, Shipment, SyncRun
from app.models.enums import Channel, IntegrationStatus, Provider, SyncStatus
from app.sync.common import (
    SyncAlreadyRunning,
    describe_unexpected,
    finish_run,
    get_cursor,
    mark_ok,
    record_failure,
    set_cursor,
    start_run,
)

logger = logging.getLogger("painel.shopee")


def keep_shopee_alive(session_factory: sessionmaker[Session]) -> bool:
    """Renova o acesso da Shopee se estiver perto de vencer. True = conexão ok."""
    with session_factory() as db:
        account = get_shopee_account(db)
        if account is None or not account.refresh_token_enc:
            return False
    try:
        ShopeeTokenProvider(session_factory).get_token()
    except NotConnected:
        return False
    except IntegrationError as exc:  # AuthExpired já marca a conta com erro
        logger.warning("Shopee: não foi possível renovar o acesso: %s", exc)
        return False
    with session_factory() as db:
        account = get_shopee_account(db)
        if account is not None:
            account.status = IntegrationStatus.OK
            account.last_error = None
            account.last_ok_at = utcnow()
            db.commit()
    return True


# --- Pedidos, envios e taxas (somente leitura) ------------------------------------

FIRST_DAYS = 15                 # primeira leitura: últimos 15 dias
CURSOR_OVERLAP = timedelta(minutes=15)
MAX_ESCROW_PER_RUN = 80         # taxas: uma consulta por pedido; o resto fica para a próxima rodada


def run_shopee_sync(session_factory: sessionmaker[Session],
                    client_factory: Callable[[str], ShopeeClient] | None = None) -> SyncRun | None:
    """Lê da Shopee: dados da loja, pedidos alterados (situação e prazo de envio) e taxas.

    Os PEDIDOS em si continuam vindo do Olist (fonte da verdade); daqui saem o prazo de
    despacho (quadro "Envios de hoje") e a taxa/valor líquido de cada venda (lucro real),
    ligados ao pedido do Olist pelo número da Shopee (order_sn).
    """
    with session_factory() as db:
        account = get_shopee_account(db)
        if account is None or not account.access_token_enc or not account.external_id:
            return None
        shop_id = account.external_id
        try:
            run = start_run(db, Provider.SHOPEE)
        except SyncAlreadyRunning:
            logger.info("Sincronização da Shopee já em andamento; pulando.")
            return None

    started = utcnow()
    counts: dict[str, int] = {}
    factory = client_factory or (lambda shop: ShopeeClient(ShopeeTokenProvider(session_factory), shop))
    try:
        with factory(shop_id) as client, session_factory() as db:
            account = get_shopee_account(db)
            _sync_shop_info(client, account)
            cursor = get_cursor(account, "cursor_pedidos")
            since = cursor - CURSOR_OVERLAP if cursor else started - timedelta(days=FIRST_DAYS)
            order_sns = list(dict.fromkeys(client.iter_order_sns(since, started)))
            counts["pedidos"] = len(order_sns)
            details = client.get_order_details(order_sns, DETAIL_FIELDS) if order_sns else []
            counts["envios"] = _save_shipments(db, details, started)
            db.commit()
            counts["taxas"] = _sync_fees(db, client, details)
            set_cursor(account, "cursor_pedidos", started)
            mark_ok(db, account)
            db.commit()
        status, error = SyncStatus.OK, None
    except (IntegrationError, NotConnected) as exc:
        status, error = SyncStatus.ERROR, str(exc)
        record_failure(session_factory, Provider.SHOPEE, exc)
    except Exception as exc:  # noqa: BLE001 - qualquer erro vira alerta, nunca derruba o worker
        logger.exception("Erro inesperado na sincronização da Shopee")
        status, error = SyncStatus.ERROR, f"Erro inesperado ao ler a Shopee: {describe_unexpected(exc)}"
        record_failure(session_factory, Provider.SHOPEE, IntegrationError(error))
    return finish_run(session_factory, run.id, Provider.SHOPEE, status, counts, error)


def _sync_shop_info(client: ShopeeClient, account) -> None:
    info = client.get_shop_info()
    if info.get("shop_name"):
        account.account_name = str(info["shop_name"])[:120]
    expires = from_timestamp(info.get("expire_time"))
    account.extra = {**(account.extra or {}), "shop_status": info.get("status"),
                     "auth_expires_at": expires.isoformat() if expires else None}


def _save_shipments(db: Session, orders: list[dict], started) -> int:
    saved = 0
    for order in orders:
        info = shipment_from(order)
        row = db.scalar(select(Shipment).where(Shipment.channel == Channel.SHOPEE,
                                               Shipment.order_ref == info.order_sn))
        if row is None:
            row = Shipment(channel=Channel.SHOPEE, shipment_id=info.shipment_id, order_ref=info.order_sn,
                           status=info.status, checked_at=started)
            db.add(row)
        row.shipment_id = info.shipment_id
        row.status, row.substatus = info.status, info.substatus
        row.logistic_type = info.logistic_type
        row.dispatch_deadline = info.dispatch_deadline or row.dispatch_deadline
        if row.shipped_at is None and info.status in ("shipped", "delivered"):
            row.shipped_at = info.shipped_at or started
        row.checked_at = started
        saved += 1
    return saved


def _sync_fees(db: Session, client: ShopeeClient, orders: list[dict]) -> int:
    """Taxa e valor líquido de cada pedido alterado (pagos e não cancelados)."""
    wanted = [str(o["order_sn"]) for o in orders if str(o.get("order_status")) not in NO_ESCROW]
    done = 0
    for order_sn in wanted[:MAX_ESCROW_PER_RUN]:
        try:
            escrow = client.get_escrow_detail(order_sn)
        except AuthExpired:
            raise
        except TemporaryError as exc:
            logger.warning("Shopee pediu pausa ao ler taxas (%s). Continua na próxima rodada.", exc)
            break
        except IntegrationError as exc:
            logger.warning("Taxa do pedido %s não pôde ser lida: %s", order_sn, exc)
            continue
        income = income_from(escrow)
        row = db.scalar(select(MarketplaceFee).where(MarketplaceFee.channel == Channel.SHOPEE,
                                                     MarketplaceFee.order_ref == order_sn))
        if row is None:
            row = MarketplaceFee(channel=Channel.SHOPEE, order_ref=order_sn)
            db.add(row)
        row.fee_cents, row.net_cents = income.fee_cents, income.net_cents
        # Liga ao pedido do Olist, se ele já chegou (senão liga quando chegar).
        for order in db.scalars(select(Order).where(Order.channel == Channel.SHOPEE,
                                                    Order.external_id == order_sn)):
            order.marketplace_fee_cents, order.fees_known = income.fee_cents, True
            if income.net_cents is not None:
                order.net_received_cents = income.net_cents
        done += 1
        db.commit()  # grava aos poucos: uma pausa no meio não perde o que já foi lido
    if len(wanted) > MAX_ESCROW_PER_RUN:
        logger.info("Shopee: %s taxas ficam para a próxima rodada.", len(wanted) - MAX_ESCROW_PER_RUN)
    return done
