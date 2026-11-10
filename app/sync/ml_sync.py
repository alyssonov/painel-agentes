"""Sincronização Mercado Livre → banco local (somente leitura).

Etapas: reputação → perguntas → taxas das vendas.
- Perguntas: todas as não respondidas + as mais recentes (para o painel
  saber quais foram respondidas hoje). Nada é respondido aqui.
- Taxas: o sale_fee de cada venda vai para `marketplace_fees` e é ligado ao
  pedido do Olist (que é a fonte da verdade dos pedidos) pelo número do
  pedido ou do pacote no Mercado Livre.
"""

import logging
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import utcnow
from app.integrations.errors import AuthExpired, IntegrationError, NotConnected, TemporaryError
from app.integrations.mercadolivre.auth import MLTokenProvider, get_ml_account
from app.integrations.mercadolivre.client import MLClient
from app.integrations.mercadolivre.mapping import (
    DISPATCHED_SUBSTATUSES,
    WAITING_DISPATCH,
    left_the_store,
    listing_rows,
    order_fee_cents,
    order_refs,
    question_status,
    reputation_label,
    reputation_metrics,
)
from app.integrations.olist.dates import parse_olist_datetime as parse_datetime
from app.models import Action, Listing, MarketplaceFee, Order, Product, Question, Shipment, SyncRun
from app.models.enums import ActionStatus, ActionType, Channel, Provider, QuestionStatus, SyncStatus
from app.integrations.mercadopago.client import MercadoPagoClient
from app.sync.ml_payments import record_order_payments, sync_payments
from app.sync.ml_postsale import sync_claims, sync_messages
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

logger = logging.getLogger("sync.ml")

FIRST_QUESTIONS_DAYS = 7
FIRST_FEES_DAYS = 15
# Taxas: relê sempre esta janela (o pedido pode chegar no Olist depois).
FEES_WINDOW_DAYS = 5
# Envios: a cada 15 min a busca de vendas volta mais dias, porque uma venda pode
# ter prazo de despacho vários dias depois (visto em 10/11/2026: vendas de 6+ dias
# atrás despachadas hoje ficavam de fora do quadro).
SHIPMENTS_WINDOW_DAYS = 15
CURSOR_OVERLAP = timedelta(minutes=10)

ClientFactory = Callable[[], MLClient]


def run_ml_sync(session_factory: sessionmaker[Session], client_factory: ClientFactory | None = None,
                mp_factory: Callable[[], MercadoPagoClient] | None = None) -> SyncRun | None:
    with session_factory() as db:
        account = get_ml_account(db)
        if account is None or not account.access_token_enc:
            return None
        try:
            run = start_run(db, Provider.ML)
        except SyncAlreadyRunning:
            logger.info("Sincronização do Mercado Livre já em andamento; pulando.")
            return None

    started = utcnow()
    counts: dict[str, int] = {}
    factory = client_factory or (lambda: MLClient(MLTokenProvider(session_factory)))
    try:
        with factory() as client, session_factory() as db:
            account = get_ml_account(db)
            seller_id = sync_profile(db, client, account)
            counts["perguntas"] = sync_questions(db, client, account, seller_id, started)
            seen_shipments: dict[str, str] = {}
            counts["taxas"] = sync_fees(db, client, account, seller_id, started, seen_shipments)
            try:
                counts["envios"] = sync_shipments(db, client, account, started, seen_shipments)
            except IntegrationError as exc:
                db.rollback()
                account = get_ml_account(db)
                logger.warning("Não foi possível ler os envios do Mercado Livre: %s", exc)
                counts["envios_erro"] = 1
            try:
                counts["anuncios"] = sync_listings(db, client, account, seller_id, started)
            except IntegrationError as exc:
                # A lista de anúncios é complementar (Vigia de estoque): uma falha aqui
                # não pode parar perguntas, reputação e taxas.
                db.rollback()
                account = get_ml_account(db)
                logger.warning("Não foi possível ler os anúncios do Mercado Livre: %s", exc)
                counts["anuncios_erro"] = 1
            # Recebimentos (Mercado Pago): complementar, uma falha não para o resto.
            try:
                mp_make = mp_factory or (lambda: MercadoPagoClient(MLTokenProvider(session_factory)))
                with mp_make() as mp:
                    counts["recebimentos"] = sync_payments(db, client, mp, account, seller_id, started)
            except IntegrationError as exc:
                db.rollback()
                account = get_ml_account(db)
                logger.warning("Não foi possível ler os recebimentos: %s", exc)
                counts["recebimentos_erro"] = 1
            # Pós-venda (Fase 6): cada parte isolada, uma falha não para as outras.
            for key, step in (("mensagens", sync_messages), ("reclamacoes", sync_claims)):
                try:
                    counts[key] = step(db, client, account, seller_id, started)
                except AuthExpired:
                    raise
                except IntegrationError as exc:
                    db.rollback()
                    account = get_ml_account(db)
                    logger.warning("Não foi possível ler %s do Mercado Livre: %s", key, exc)
                    counts[f"{key}_erro"] = 1
            mark_ok(db, account)
            db.commit()
        status, error = SyncStatus.OK, None
    except (IntegrationError, NotConnected) as exc:
        status, error = SyncStatus.ERROR, str(exc)
        record_failure(session_factory, Provider.ML, exc)
    except Exception as exc:  # noqa: BLE001 - qualquer erro vira alerta, nunca derruba o worker
        logger.exception("Erro inesperado na sincronização do Mercado Livre")
        status, error = SyncStatus.ERROR, f"Erro inesperado ao ler o Mercado Livre: {describe_unexpected(exc)}"
        record_failure(session_factory, Provider.ML, IntegrationError(error))
    return finish_run(session_factory, run.id, Provider.ML, status, counts, error)


def sync_profile(db: Session, client: MLClient, account) -> str:
    """Dados da conta e reputação (card "Reputação" da Visão geral)."""
    me = client.get_me()
    seller_id = str(me.get("id") or account.external_id or "")
    if not seller_id:
        raise IntegrationError("O Mercado Livre não informou o número da conta.")
    account.external_id = seller_id
    account.account_name = me.get("nickname") or account.account_name
    account.extra = {**(account.extra or {}), "reputation_label": reputation_label(me),
                     "reputation_metrics": reputation_metrics(me)}
    db.commit()
    return seller_id


def sync_questions(db: Session, client: MLClient, account, seller_id: str, started) -> int:
    since = get_cursor(account, "cursor_perguntas")
    oldest = (since - CURSOR_OVERLAP) if since else started - timedelta(days=FIRST_QUESTIONS_DAYS)
    seen: dict[str, dict] = {}
    # 1) Todas as que ainda esperam resposta.
    for q in client.iter_questions(seller_id, status="UNANSWERED"):
        seen[str(q.get("id"))] = q
    unanswered_ids = set(seen)
    # 2) As mais recentes de qualquer situação, até chegar no período já lido.
    for q in client.iter_questions(seller_id):
        asked = parse_datetime(q.get("date_created"))
        if asked and asked < oldest:
            break
        seen[str(q.get("id"))] = q
    # 3) Perguntas que o painel acha que estão abertas, mas que não estão mais
    #    "sem resposta" no Mercado Livre (ex.: respondidas direto lá): confere uma a uma.
    open_local = db.scalars(select(Question).where(
        Question.channel == Channel.ML, Question.is_demo.is_(False),
        Question.status.in_([QuestionStatus.NEW, QuestionStatus.PROPOSED, QuestionStatus.HUMAN,
                             QuestionStatus.REVIEW])))
    for question in open_local:
        if question.external_id in unanswered_ids or question.external_id in seen:
            continue
        current = client.get_question(question.external_id)
        if current:
            seen[question.external_id] = current

    titles_needed: set[str] = set()
    count = 0
    for qid, q in seen.items():
        if not qid or qid == "None":
            continue
        question = db.scalar(select(Question).where(Question.channel == Channel.ML, Question.external_id == qid))
        new_status = question_status(q.get("status"))
        if question is None:
            question = Question(channel=Channel.ML, external_id=qid, text=q.get("text") or "",
                                asked_at=parse_datetime(q.get("date_created")) or started, status=new_status)
            db.add(question)
        elif new_status == QuestionStatus.NEW and question.status in (
                QuestionStatus.PROPOSED, QuestionStatus.HUMAN, QuestionStatus.SIMULATED, QuestionStatus.CLOSED):
            pass  # já tratada no painel (fila, simulação ou tirada da fila): mantém a situação de lá
        else:
            question.status = new_status
        question.text = q.get("text") or question.text
        question.listing_external_id = q.get("item_id") or question.listing_external_id
        answer = q.get("answer") or {}
        if new_status == QuestionStatus.ANSWERED:
            question.answer_text = answer.get("text") or question.answer_text
            question.answered_at = parse_datetime(answer.get("date_created")) or question.answered_at or started
        question.is_demo = False
        if question.listing_external_id and not question.listing_title:
            titles_needed.add(question.listing_external_id)
        count += 1
    db.flush()

    if titles_needed:
        try:
            titles = {i.get("id"): i.get("title") for i in client.get_items(sorted(titles_needed))}
        except IntegrationError as exc:  # sem os títulos, as perguntas entram assim mesmo
            logger.warning("Não foi possível ler os títulos dos anúncios: %s", exc)
            titles = {}
        for question in db.scalars(select(Question).where(
                Question.channel == Channel.ML, Question.listing_external_id.in_(titles_needed))):
            question.listing_title = titles.get(question.listing_external_id) or question.listing_title
    _close_stale_suggestions(db)
    set_cursor(account, "cursor_perguntas", started)
    db.commit()
    return count


def _close_stale_suggestions(db: Session) -> None:
    """Sugestões na fila de perguntas que já foram respondidas (ou fechadas) no marketplace saem da fila."""
    from app.core import actions  # import local: o gateway só é usado aqui para descartar (nada é enviado)

    pending = db.scalars(select(Action).where(
        Action.type == ActionType.ANSWER_QUESTION,
        Action.status.in_([ActionStatus.PENDING, ActionStatus.FAILED])))
    for action in pending:
        question = db.get(Question, int(action.target_id))
        if question is not None and question.status in (QuestionStatus.ANSWERED, QuestionStatus.CLOSED):
            actions.reject(db, action, actor="system",
                           reason="Respondida (ou encerrada) direto no Mercado Livre")


def sync_fees(db: Session, client: MLClient, account, seller_id: str, started,
              shipments: dict[str, str] | None = None) -> int:
    """Taxas das vendas. Se `shipments` for passado, também anota os envios vistos (id → pedido)."""
    first = get_cursor(account, "cursor_taxas") is None
    days = FIRST_FEES_DAYS if first else FEES_WINDOW_DAYS
    if shipments is not None and _shipments_due(account, started):
        days = max(days, SHIPMENTS_WINDOW_DAYS)
    created_from = started - timedelta(days=days)
    # Soma por pacote: no Olist, um pacote com vários pedidos costuma virar um pedido só.
    fees: dict[str, int] = {}
    shipments = shipments if shipments is not None else {}
    for order in client.iter_orders(seller_id, created_from=created_from):
        shipping_id = (order.get("shipping") or {}).get("id")
        if shipping_id and order.get("status") not in ("cancelled", "invalid"):
            shipments[str(shipping_id)] = str(order.get("pack_id") or order.get("id"))
        record_order_payments(db, order, str(order.get("pack_id") or order.get("id")))
        fee = order_fee_cents(order)
        for ref in order_refs(order):
            fees[ref] = fees.get(ref, 0) + fee if ref == str(order.get("pack_id")) else fee
    for ref, fee_cents in fees.items():
        row = db.scalar(select(MarketplaceFee).where(MarketplaceFee.channel == Channel.ML,
                                                     MarketplaceFee.order_ref == ref))
        if row is None:
            row = MarketplaceFee(channel=Channel.ML, order_ref=ref)
            db.add(row)
        row.fee_cents = fee_cents
    db.flush()
    # Liga as taxas aos pedidos já importados do Olist.
    for order in db.scalars(select(Order).where(Order.channel == Channel.ML, Order.external_id.in_(list(fees)))):
        order.marketplace_fee_cents = fees[order.external_id]
        order.fees_known = True
    set_cursor(account, "cursor_taxas", started)
    db.commit()
    return len(fees)


SHIPPED = {"shipped", "delivered"}
SHIPMENTS_EVERY = timedelta(minutes=15)
MAX_SHIPMENTS_PER_RUN = 150


def _shipments_due(account, started) -> bool:
    """Hora de reconferir os envios (e de buscar vendas mais antigas)? A cada 15 minutos."""
    last = get_cursor(account, "cursor_envios")
    return not last or started - last >= SHIPMENTS_EVERY


def _left_store_at(data: dict):
    """Quando o pacote saiu da loja: data de envio, ou da coleta/entrega na agência no histórico do detalhe."""
    history = data.get("status_history") or {}
    shipped = parse_datetime(history.get("date_shipped")) or parse_datetime(data.get("date_shipped"))
    if shipped:
        return shipped
    for entry in data.get("substatus_history") or []:
        if isinstance(entry, dict) and entry.get("substatus") in DISPATCHED_SUBSTATUSES:
            return parse_datetime(entry.get("date"))
    return None


def sync_shipments(db: Session, client: MLClient, account, started, new: dict[str, str]) -> int:
    """Situação e prazo de despacho dos envios que ainda não saíram.

    Cuidados (aprendidos no primeiro uso real, com ~70 envios pendentes):
    - o prazo (SLA) só é consultado uma vez por envio;
    - os mais antigos sem conferir vão primeiro, e o progresso é gravado aos poucos;
    - se o Mercado Livre pedir pausa (limite de consultas), paramos SEM perder o
      que já foi lido — o resto fica para a próxima rodada;
    - um envio com erro não trava os outros.
    """
    recheck_pending = _shipments_due(account, started)
    known = set(db.scalars(select(Shipment.shipment_id).where(Shipment.channel == Channel.ML)))
    to_check: dict[str, str] = {sid: ref for sid, ref in new.items() if sid not in known}
    if recheck_pending:
        for row in db.scalars(select(Shipment).where(
                Shipment.channel == Channel.ML, Shipment.status.in_(WAITING_DISPATCH),
                # já coletado: só relê uma vez, para descobrir quando saiu da loja
                or_(Shipment.substatus.is_(None), Shipment.substatus.not_in(DISPATCHED_SUBSTATUSES),
                    Shipment.shipped_at.is_(None)))
                .order_by(Shipment.checked_at)):
            to_check.setdefault(row.shipment_id, row.order_ref)
    if not to_check:
        return 0

    checked = failed = 0
    for shipment_id, order_ref in list(to_check.items())[:MAX_SHIPMENTS_PER_RUN]:
        row = db.scalar(select(Shipment).where(Shipment.channel == Channel.ML, Shipment.shipment_id == shipment_id))
        try:
            data = client.get_shipment(shipment_id) or {}
            status = data.get("status")
            logistic = data.get("logistic_type") or (data.get("logistic") or {}).get("type")
            sla = None
            needs_deadline = row is None or row.dispatch_deadline is None
            if status in WAITING_DISPATCH and logistic != "fulfillment" and needs_deadline:
                sla = client.get_shipment_sla(shipment_id) or {}
        except AuthExpired:
            raise
        except TemporaryError as exc:
            logger.warning("Mercado Livre pediu pausa ao ler envios (%s). Continua na próxima rodada.", exc)
            break
        except IntegrationError as exc:
            failed += 1
            logger.warning("Envio %s não pôde ser lido: %s", shipment_id, exc)
            continue
        if not data:
            continue
        if row is None:
            row = Shipment(channel=Channel.ML, shipment_id=shipment_id, order_ref=order_ref,
                           status=status or "desconhecido", checked_at=started)
            db.add(row)
        was_out = row.shipped_at is None and left_the_store(row.status, row.substatus)
        row.status = status or row.status
        row.substatus = data.get("substatus")
        row.logistic_type = logistic or row.logistic_type
        if sla:
            row.dispatch_deadline = parse_datetime(sla.get("expected_date")) or row.dispatch_deadline
        if left_the_store(row.status, row.substatus) and row.shipped_at is None:
            # Sem data no Mercado Livre: se acabou de sair, é "agora"; se já tinha saído
            # antes (envios gravados por versões antigas), usa o prazo como aproximação.
            fallback = min(row.dispatch_deadline, started) if was_out and row.dispatch_deadline else started
            row.shipped_at = _left_store_at(data) or fallback
        row.checked_at = utcnow()
        checked += 1
        if checked % 10 == 0:
            db.commit()  # grava aos poucos: uma pausa no meio não perde o que já foi lido
    if recheck_pending:
        set_cursor(account, "cursor_envios", started)
    db.commit()
    if failed:
        logger.warning("%s envio(s) do Mercado Livre não puderam ser lidos nesta rodada.", failed)
    return checked


# Anúncios: relidos de hora em hora (estoque muda pouco e o Olist é a fonte da verdade).
LISTINGS_EVERY = timedelta(minutes=60)


def sync_listings(db: Session, client: MLClient, account, seller_id: str, started) -> int:
    """Estoque, preço e SKU dos anúncios ativos (para o Vigia de estoque comparar com o Olist)."""
    last = get_cursor(account, "cursor_anuncios")
    if last and started - last < LISTINGS_EVERY:
        return 0
    ids = list(client.iter_my_item_ids(seller_id))
    by_sku = dict(db.execute(select(Product.sku, Product.id).where(Product.is_demo.is_(False))).all())
    seen: set[str] = set()
    for item in client.get_items_full(ids):
        for row in listing_rows(item):
            seen.add(row["external_id"])
            listing = db.scalar(select(Listing).where(Listing.channel == Channel.ML,
                                                      Listing.external_id == row["external_id"]))
            if listing is None:
                listing = Listing(channel=Channel.ML, external_id=row["external_id"], item_id=row["item_id"],
                                  title=row["title"], status=row["status"], synced_at=started)
                db.add(listing)
            for key in ("item_id", "title", "price_cents", "available_quantity", "status", "permalink",
                        "fulfillment", "sku", "variation_label"):
                setattr(listing, key, row[key])
            listing.product_id = by_sku.get(row["sku"]) if row["sku"] else None
            listing.synced_at = started
    # Anúncios que não estão mais ativos: marcados como tal (não entram na comparação).
    for listing in db.scalars(select(Listing).where(Listing.channel == Channel.ML,
                                                    Listing.external_id.not_in(seen) if seen else True)):
        listing.status = "inativo"
    set_cursor(account, "cursor_anuncios", started)
    db.commit()
    return len(seen)
