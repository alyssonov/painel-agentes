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

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import utcnow
from app.integrations.errors import IntegrationError, NotConnected
from app.integrations.mercadolivre.auth import MLTokenProvider, get_ml_account
from app.integrations.mercadolivre.client import MLClient
from app.integrations.mercadolivre.mapping import order_fee_cents, order_refs, question_status, reputation_label
from app.integrations.olist.dates import parse_olist_datetime as parse_datetime
from app.models import Action, MarketplaceFee, Order, Question, SyncRun
from app.models.enums import ActionStatus, ActionType, Channel, Provider, QuestionStatus, SyncStatus
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
FEES_WINDOW_DAYS = 3
CURSOR_OVERLAP = timedelta(minutes=10)

ClientFactory = Callable[[], MLClient]


def run_ml_sync(session_factory: sessionmaker[Session],
                client_factory: ClientFactory | None = None) -> SyncRun | None:
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
            counts["taxas"] = sync_fees(db, client, account, seller_id, started)
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
    account.extra = {**(account.extra or {}), "reputation_label": reputation_label(me)}
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


def sync_fees(db: Session, client: MLClient, account, seller_id: str, started) -> int:
    first = get_cursor(account, "cursor_taxas") is None
    created_from = started - timedelta(days=FIRST_FEES_DAYS if first else FEES_WINDOW_DAYS)
    # Soma por pacote: no Olist, um pacote com vários pedidos costuma virar um pedido só.
    fees: dict[str, int] = {}
    for order in client.iter_orders(seller_id, created_from=created_from):
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
