"""Sincronização Olist → banco local (somente leitura no Olist).

Ordem: produtos → estoque → pedidos → notas fiscais. Cada etapa grava no banco
assim que termina, então uma falha no meio não perde o que já foi lido.

Para gastar poucas requisições, depois da primeira vez só buscamos o que
mudou desde a última sincronização (com 10 minutos de folga).
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.core import audit
from app.core.db import utcnow
from app.integrations.errors import AuthExpired, IntegrationError, NotConnected
from app.integrations.olist.auth import OlistTokenProvider, get_olist_account
from app.integrations.olist.client import OlistClient
from app.integrations.olist.dates import parse_olist_datetime, to_cents
from app.integrations.olist.mapping import (
    channel_for,
    invoice_status_label,
    kanban_for,
    order_status_label,
    problem_note_for,
    product_cost,
    short_customer_name,
    to_int,
)
from app.models import Alert, Invoice, Order, OrderItem, Product, StockSnapshot, SyncRun
from app.models.enums import AlertSeverity, AlertStatus, IntegrationStatus, Provider, SyncStatus

logger = logging.getLogger("sync.olist")

# Uma sincronização "rodando" há mais que isto é considerada travada.
STALE_RUN = timedelta(minutes=30)
# Folga ao buscar "o que mudou desde": evita perder algo por diferença de relógio.
CURSOR_OVERLAP = timedelta(minutes=10)
# Notas são relidas desta janela a cada vez (a situação muda depois de criada).
INVOICE_WINDOW_DAYS = 7
ALERT_SOURCE = "olist"

ClientFactory = Callable[[], OlistClient]


class SyncAlreadyRunning(Exception):
    """Outra sincronização do Olist já está em andamento."""


def _default_client_factory(session_factory: sessionmaker[Session]) -> ClientFactory:
    return lambda: OlistClient(OlistTokenProvider(session_factory))


def _cursor(account, key: str) -> datetime | None:
    value = (account.extra or {}).get(key)
    return datetime.fromisoformat(value) if value else None


def _set_cursor(account, key: str, value: datetime) -> None:
    account.extra = {**(account.extra or {}), key: value.isoformat()}


def _start_run(db: Session) -> SyncRun:
    running = db.scalar(select(SyncRun).where(
        SyncRun.provider == Provider.OLIST, SyncRun.status == SyncStatus.RUNNING,
        SyncRun.started_at > utcnow() - STALE_RUN))
    if running:
        raise SyncAlreadyRunning()
    # Execuções antigas que ficaram "rodando" (ex.: computador desligou no meio).
    db.execute(update(SyncRun).where(
        SyncRun.provider == Provider.OLIST, SyncRun.status == SyncStatus.RUNNING
    ).values(status=SyncStatus.ERROR, error="Interrompida", finished_at=utcnow()))
    run = SyncRun(provider=Provider.OLIST, status=SyncStatus.RUNNING, counts={})
    db.add(run)
    db.commit()
    return run


def run_olist_sync(session_factory: sessionmaker[Session],
                   client_factory: ClientFactory | None = None) -> SyncRun | None:
    """Executa uma sincronização completa. Retorna o registro da execução (ou None se não conectado)."""
    with session_factory() as db:
        account = get_olist_account(db)
        if account is None or not account.access_token_enc:
            return None
        try:
            run = _start_run(db)
        except SyncAlreadyRunning:
            logger.info("Sincronização do Olist já em andamento; pulando.")
            return None

    started = utcnow()
    counts: dict[str, int] = {}
    factory = client_factory or _default_client_factory(session_factory)
    try:
        with factory() as client, session_factory() as db:
            account = get_olist_account(db)
            first_sync = account.last_ok_at is None
            counts["produtos"] = sync_products(db, client, account, started)
            counts["estoque"] = sync_stock(db, client, account, started)
            counts["pedidos"] = sync_orders(db, client, account, started)
            counts["notas"] = sync_invoices(db, client, first_sync=first_sync)
            account.status = IntegrationStatus.OK
            account.last_ok_at = utcnow()
            account.last_error = None
            _resolve_alerts(db)
            db.commit()
        status, error = SyncStatus.OK, None
    except (IntegrationError, NotConnected) as exc:
        status, error = SyncStatus.ERROR, str(exc)
        _record_failure(session_factory, exc)
    except Exception as exc:  # noqa: BLE001 - qualquer erro vira alerta, nunca derruba o worker
        logger.exception("Erro inesperado na sincronização do Olist")
        status, error = SyncStatus.ERROR, "Erro inesperado ao ler o Olist (detalhes no log do sistema)."
        _record_failure(session_factory, IntegrationError(error))

    with session_factory() as db:
        run = db.get(SyncRun, run.id)
        run.status, run.error, run.counts, run.finished_at = status, error, counts, utcnow()
        audit.record(db, actor="system", event=f"olist.sincronizacao.{status}",
                     target_type="sync_run", target_id=run.id, details={**counts, "erro": error})
        db.commit()
        return run


def _record_failure(session_factory: sessionmaker[Session], exc: Exception) -> None:
    """Marca a conta com erro e abre um alerta (um só, sem repetir a cada tentativa)."""
    with session_factory() as db:
        account = get_olist_account(db)
        if account is not None:
            account.status = IntegrationStatus.ERROR
            account.last_error = str(exc)
        already_open = db.scalar(select(Alert).where(
            Alert.source == ALERT_SOURCE, Alert.status == AlertStatus.OPEN))
        title = ("Olist desconectado: é preciso autorizar de novo" if isinstance(exc, AuthExpired)
                 else "Não foi possível atualizar os dados do Olist")
        if already_open:
            already_open.title, already_open.detail = title, str(exc)
        else:
            db.add(Alert(source=ALERT_SOURCE, severity=AlertSeverity.CRITICAL, title=title,
                         detail=str(exc), target_type="integracao", target_id="olist"))
        db.commit()


def _resolve_alerts(db: Session) -> None:
    for alert in db.scalars(select(Alert).where(
            Alert.source == ALERT_SOURCE, Alert.status == AlertStatus.OPEN)):
        alert.status = AlertStatus.RESOLVED
        alert.resolved_at = utcnow()


# --- Etapas --------------------------------------------------------------------

def sync_products(db: Session, client: OlistClient, account, started: datetime) -> int:
    since = _cursor(account, "cursor_produtos")
    count = 0
    for item in client.iter_products(changed_since=since - CURSOR_OVERLAP if since else None):
        if item.get("situacao") == "E" or not item.get("id"):  # excluído no Olist
            continue
        olist_id = str(item["id"])
        product = db.scalar(select(Product).where(Product.olist_id == olist_id))
        sku = (item.get("sku") or "").strip() or f"olist-{olist_id}"
        if product is None:
            # Se já existir um produto com o mesmo SKU (sem id do Olist), aproveita.
            product = db.scalar(select(Product).where(Product.sku == sku, Product.is_demo.is_(False)))
            if product is None:
                product = Product(sku=sku, name=item.get("descricao") or sku)
                db.add(product)
            product.olist_id = olist_id
        precos = item.get("precos") or {}
        product.sku = sku
        product.name = item.get("descricao") or product.name
        product.price_cents = to_cents(precos.get("preco"))
        cost = product_cost(precos)
        if cost is not None:
            product.cost_cents = to_cents(cost)
        product.is_active = item.get("situacao", "A") == "A"
        product.is_demo = False
        count += 1
        if count % 200 == 0:
            db.flush()
    _set_cursor(account, "cursor_produtos", started)
    db.commit()
    return count


def sync_stock(db: Session, client: OlistClient, account, started: datetime) -> int:
    since = _cursor(account, "cursor_estoque")
    by_olist_id = dict(db.execute(select(Product.olist_id, Product.id).where(Product.olist_id.is_not(None))).all())
    count = 0
    for item in client.iter_stock(changed_since=since - CURSOR_OVERLAP if since else None):
        product_id = by_olist_id.get(str(item.get("idProduto")))
        if product_id is None:
            continue
        db.add(StockSnapshot(
            product_id=product_id,
            quantity=int(float(item.get("saldo") or 0)),
            reserved=int(float(item.get("reservado") or 0)),
            captured_at=started,
        ))
        count += 1
    _set_cursor(account, "cursor_estoque", started)
    db.commit()
    return count


def sync_orders(db: Session, client: OlistClient, account, started: datetime) -> int:
    since = _cursor(account, "cursor_pedidos")
    if since:
        listing = client.iter_orders(updated_since=since - CURSOR_OVERLAP)
    else:
        days = get_settings().olist_initial_days
        listing = client.iter_orders(created_since=started - timedelta(days=days))
    products = dict(db.execute(select(Product.olist_id, Product.id).where(Product.olist_id.is_not(None))).all())
    count = 0
    for summary in listing:
        order_id = summary.get("id")
        if not order_id:
            continue
        detail = client.get_order(order_id)
        if not detail:
            continue
        upsert_order(db, summary, detail, products)
        count += 1
        db.commit()  # grava pedido a pedido: se cair no meio, o que foi lido fica salvo
    _set_cursor(account, "cursor_pedidos", started)
    db.commit()
    return count


def upsert_order(db: Session, summary: dict, detail: dict, products: dict[str, int]) -> Order:
    """Cria ou atualiza um pedido a partir da listagem + detalhe do Olist."""
    olist_id = str(detail.get("id") or summary.get("id"))
    order = db.scalar(select(Order).where(Order.olist_id == olist_id))
    if order is None:
        order = Order(olist_id=olist_id)
        db.add(order)

    ecommerce = detail.get("ecommerce") or summary.get("ecommerce") or {}
    situacao = detail.get("situacao", summary.get("situacao"))
    number = detail.get("numeroPedido") or summary.get("numeroPedido")
    order.channel = channel_for(ecommerce)
    order.external_id = str(ecommerce.get("numeroPedidoEcommerce") or ecommerce.get("numeroPedidoCanalVenda")
                            or f"olist-{number}")
    order.olist_number = str(number) if number is not None else None
    order.customer_short_name = short_customer_name((detail.get("cliente") or summary.get("cliente") or {}).get("nome"))
    order.olist_status = order_status_label(situacao)
    order.kanban_status = kanban_for(situacao)
    order.problem_note = problem_note_for(situacao)
    order.items_total_cents = to_cents(detail.get("valorTotalProdutos"))
    # Frete pago pela loja (campo informativo do Olist).
    order.shipping_cost_cents = to_cents(detail.get("valorFretePagoEmpresa"))
    order.marketplace_fee_cents = order.marketplace_fee_cents or 0
    order.ordered_at = (parse_olist_datetime(detail.get("dataCriacao") or summary.get("dataCriacao"))
                        or parse_olist_datetime(detail.get("data")) or utcnow())
    order.is_demo = False

    # Itens: substitui todos (o detalhe do Olist é a versão atual do pedido).
    order.items.clear()
    for item in detail.get("itens") or []:
        produto = item.get("produto") or {}
        if produto.get("tipo") == "S":  # serviço não entra no custo de mercadoria
            continue
        order.items.append(OrderItem(
            product_id=products.get(str(produto.get("id"))),
            sku=produto.get("sku") or "",
            title=produto.get("descricao") or "Item",
            quantity=int(float(item.get("quantidade") or 0)),
            unit_price_cents=to_cents(item.get("valorUnitario")),
        ))
    if not order.items_total_cents:
        order.items_total_cents = sum(i.quantity * i.unit_price_cents for i in order.items)
    return order


def sync_invoices(db: Session, client: OlistClient, *, first_sync: bool) -> int:
    days = get_settings().olist_initial_days if first_sync else INVOICE_WINDOW_DAYS
    since = utcnow() - timedelta(days=days)
    count = 0
    for item in client.iter_invoices(created_since=since):
        if not item.get("id"):
            continue
        olist_id = str(item["id"])
        # Consultas primeiro, antes de criar a nota (evita gravar uma nota incompleta).
        invoice = db.scalar(select(Invoice).where(Invoice.olist_id == olist_id))
        origem = item.get("origem") or {}
        order_olist_id = str(origem["id"]) if origem.get("tipo") == "venda" and origem.get("id") else None
        order_id = (db.scalar(select(Order.id).where(Order.olist_id == order_olist_id))
                    if order_olist_id else None)
        if invoice is None:
            invoice = Invoice(olist_id=olist_id)
            db.add(invoice)
        if order_olist_id:
            invoice.order_olist_id = order_olist_id
            invoice.order_id = order_id
        invoice.number = item.get("numero")
        invoice.series = item.get("serie")
        invoice.access_key = item.get("chaveAcesso")
        invoice.status_code = to_int(item.get("situacao")) or 0
        invoice.status_label = invoice_status_label(item.get("situacao"))
        invoice.issued_at = parse_olist_datetime(item.get("dataEmissao"))
        invoice.total_cents = to_cents(item.get("valor"))
        count += 1
    db.commit()
    return count
