"""Leitura do pós-venda do Mercado Livre: conversas dos pedidos e reclamações.

Só LÊ. Responder ao comprador é uma Ação que passa pelo ActionGateway.
As mensagens são lidas sempre com mark_as_read=false: o painel não marca nada
como lido no Mercado Livre.

Documentação oficial consultada em 10/11/2026:
  https://developers.mercadolivre.com.br/pt_br/mensagens-post-venda
  https://developers.mercadolivre.com.br/pt_br/mensagens-pendentes
  https://developers.mercadolivre.com.br/pt_br/mensagens-bloqueadas
  https://developers.mercadolivre.com.br/pt_br/gerenciar-reclamacoes
"""

import logging
import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.integrations.errors import AuthExpired, IntegrationError, TemporaryError
from app.integrations.mercadolivre.client import MLClient
from app.integrations.olist.dates import parse_olist_datetime as parse_datetime
from app.models import Action, Claim, Conversation, ConversationMessage
from app.models.enums import ActionStatus, ActionType, Channel, ConversationStatus
from app.sync.common import get_cursor, set_cursor

logger = logging.getLogger("sync.ml.posvenda")

RECHECK_EVERY = timedelta(minutes=15)
MAX_CONVERSATIONS_PER_RUN = 40
MAX_RECHECK = 30
WAITING = (ConversationStatus.NEW, ConversationStatus.PROPOSED, ConversationStatus.HUMAN)
PACK_RE = re.compile(r"/packs/(\d+)/")


def _due(account, cursor: str, started) -> bool:
    last = get_cursor(account, cursor)
    return not last or started - last >= RECHECK_EVERY


def close_pending_actions(db: Session, conversation: Conversation, reason: str) -> None:
    """Tira da fila a sugestão de resposta desta conversa (nada é enviado)."""
    from app.core import actions  # o gateway aqui só DESCARTA

    for action in db.scalars(select(Action).where(
            Action.type == ActionType.SEND_MESSAGE, Action.target_id == str(conversation.id),
            Action.status.in_([ActionStatus.PENDING, ActionStatus.FAILED]))):
        actions.reject(db, action, actor="system", reason=reason)


def _message_text(message: dict) -> str:
    text = message.get("text")
    if isinstance(text, dict):
        text = text.get("plain")
    return (text or "").strip()


def _message_date(message: dict):
    dates = message.get("message_date") or {}
    return (parse_datetime(dates.get("created")) or parse_datetime(dates.get("received"))
            or parse_datetime(message.get("date_created")) or parse_datetime(message.get("date")))


def apply_conversation(db: Session, pack_ref: str, data: dict, seller_id: str, now) -> Conversation:
    """Grava a conversa lida do Mercado Livre e decide se ela espera resposta da loja."""
    conversation = db.scalar(select(Conversation).where(
        Conversation.channel == Channel.ML, Conversation.pack_ref == pack_ref))
    if conversation is None:
        conversation = Conversation(channel=Channel.ML, pack_ref=pack_ref, status=ConversationStatus.ANSWERED,
                                    checked_at=now)
        db.add(conversation)
        db.flush()
    remote = data.get("conversation_status") or {}
    conversation.remote_status = remote.get("status")
    conversation.blocked_reason = remote.get("substatus") if remote.get("status") == "blocked" else None
    claim = remote.get("claim_id") or (remote.get("claim_ids") or [None])[0]
    conversation.claim_ref = str(claim) if claim else None
    if data.get("seller_max_message_length"):
        conversation.max_chars = int(data["seller_max_message_length"])

    known = {m.external_id for m in conversation.messages}
    new_from_buyer = False
    for message in data.get("messages") or []:
        external_id = str(message.get("id") or message.get("message_id") or "")
        text, sent_at = _message_text(message), _message_date(message)
        if not external_id or external_id in known or not text or sent_at is None:
            continue
        if str(message.get("status") or "").lower() == "rejected":
            continue
        from_seller = str((message.get("from") or {}).get("user_id") or "") == seller_id
        conversation.messages.append(ConversationMessage(
            external_id=external_id, from_seller=from_seller, text=text, sent_at=sent_at))
        known.add(external_id)
        new_from_buyer = new_from_buyer or not from_seller
    db.flush()

    ordered = sorted(conversation.messages, key=lambda m: m.sent_at)
    if ordered:
        conversation.last_message_at = ordered[-1].sent_at
        conversation.last_from_buyer = not ordered[-1].from_seller

    status = conversation.status
    if conversation.remote_status == "blocked":
        if status in WAITING:
            close_pending_actions(db, conversation, "O Mercado Livre bloqueou a conversa")
        conversation.status = ConversationStatus.BLOCKED
    elif conversation.last_from_buyer:
        if status not in WAITING:
            conversation.status = ConversationStatus.NEW
        elif new_from_buyer and status in (ConversationStatus.PROPOSED, ConversationStatus.HUMAN):
            # Chegou mensagem nova: a sugestão antiga pode não servir mais.
            close_pending_actions(db, conversation, "Chegou mensagem nova do comprador")
            conversation.status = ConversationStatus.NEW
    elif status in WAITING or status == ConversationStatus.BLOCKED:
        close_pending_actions(db, conversation, "Respondida direto no Mercado Livre")
        conversation.status = ConversationStatus.ANSWERED
    conversation.checked_at = now
    return conversation


def sync_messages(db: Session, client: MLClient, account, seller_id: str, started) -> int:
    """Conversas com mensagem nova do comprador (+ reconferência das que esperam resposta)."""
    packs: list[str] = []
    for entry in client.get_unread_conversations():
        match = PACK_RE.search(str(entry.get("resource") or ""))
        if match and match.group(1) not in packs:
            packs.append(match.group(1))
    recheck = _due(account, "cursor_mensagens", started)
    if recheck:
        for conversation in db.scalars(select(Conversation).where(
                Conversation.channel == Channel.ML, Conversation.is_demo.is_(False),
                Conversation.status.in_(WAITING)).order_by(Conversation.checked_at).limit(MAX_RECHECK)):
            if conversation.pack_ref not in packs:
                packs.append(conversation.pack_ref)

    checked = 0
    for pack_ref in packs[:MAX_CONVERSATIONS_PER_RUN]:
        try:
            data = client.get_pack_messages(pack_ref, seller_id)
        except AuthExpired:
            raise
        except TemporaryError as exc:
            logger.warning("Mercado Livre pediu pausa ao ler mensagens (%s). Continua na próxima rodada.", exc)
            break
        except IntegrationError as exc:
            logger.warning("Conversa do pacote %s não pôde ser lida: %s", pack_ref, exc)
            continue
        apply_conversation(db, pack_ref, data, seller_id, utcnow())
        checked += 1
        db.commit()
    if recheck:
        set_cursor(account, "cursor_mensagens", started)
    db.commit()
    return checked


def _seller_actions(claim: dict) -> list[dict]:
    for player in claim.get("players") or []:
        if player.get("role") == "respondent":
            return [{"action": a.get("action"), "mandatory": bool(a.get("mandatory")), "due_date": a.get("due_date")}
                    for a in player.get("available_actions") or [] if a.get("action")]
    return []


def sync_claims(db: Session, client: MLClient, account, seller_id: str, started) -> int:
    """Reclamações abertas contra a loja (a cada 15 minutos)."""
    if not _due(account, "cursor_reclamacoes", started):
        return 0
    now = utcnow()
    seen: set[str] = set()
    for data in client.iter_open_claims(seller_id):
        external_id = str(data.get("id") or "")
        if not external_id:
            continue
        seen.add(external_id)
        row = db.scalar(select(Claim).where(Claim.channel == Channel.ML, Claim.external_id == external_id))
        changed = row is None or row.remote_updated != data.get("last_updated") or row.status != "opened"
        if row is None:
            row = Claim(channel=Channel.ML, external_id=external_id, status="opened", checked_at=now)
            db.add(row)
        row.status = data.get("status") or "opened"
        row.type, row.stage, row.reason_id = data.get("type"), data.get("stage"), data.get("reason_id")
        row.order_ref = str(data["resource_id"]) if data.get("resource_id") else row.order_ref
        row.opened_at = parse_datetime(data.get("date_created")) or row.opened_at
        row.seller_actions = _seller_actions(data)
        if changed:
            detail = client.get_claim_detail(external_id)
            row.title, row.problem = detail.get("title"), detail.get("problem")
            row.description = detail.get("description")
            row.action_responsible = detail.get("action_responsible")
            row.due_date = parse_datetime(detail.get("due_date"))
            try:
                row.affects_reputation = client.get_claim_reputation(external_id).get("affects_reputation")
            except (AuthExpired, TemporaryError):
                raise
            except IntegrationError:
                row.affects_reputation = None  # informação complementar: não trava a leitura
            row.remote_updated = data.get("last_updated")
        row.checked_at = now
    # As que não estão mais na lista de abertas foram encerradas.
    for row in db.scalars(select(Claim).where(Claim.channel == Channel.ML, Claim.status == "opened",
                                              Claim.is_demo.is_(False))):
        if row.external_id not in seen:
            row.status, row.checked_at = "closed", now
    set_cursor(account, "cursor_reclamacoes", started)
    db.commit()
    return len(seen)
