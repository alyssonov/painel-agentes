"""Seção Pós-venda: mensagens dos compradores (com aprovação) e reclamações abertas."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.postsale import CLAIM_STAGES, blocked_text, order_for, shipment_text
from app.core import actions
from app.core.db import get_db, utcnow
from app.core.timeutil import local_day_bounds_utc, local_today, to_local
from app.models import Action, Claim, Conversation, Shipment
from app.models.enums import CHANNEL_LABELS, ActionStatus, ActionType, Channel, ConversationStatus
from app.models.user import User
from app.web.auth import current_user, verify_csrf
from app.web.templating import flash, render

router = APIRouter(prefix="/pos-venda")

WAITING = [ConversationStatus.NEW, ConversationStatus.PROPOSED, ConversationStatus.HUMAN]
LAST_MESSAGES = 6
REASON_LABELS = {"PNR": "Produto não recebido", "PDD": "Produto diferente ou com defeito", "CS": "Compra cancelada"}


def _when(dt) -> str:
    return to_local(dt).strftime("%d/%m %H:%M") if dt else ""


def _conversation_view(db: Session, conversation: Conversation) -> dict:
    order = order_for(db, conversation)
    shipment = db.scalar(select(Shipment).where(Shipment.channel == conversation.channel,
                                                Shipment.order_ref == conversation.pack_ref))
    items = ", ".join(f"{i.quantity} × {i.title}" for i in order.items[:3]) if order and order.items else None
    recent = sorted(conversation.messages, key=lambda m: m.sent_at)[-LAST_MESSAGES:]
    return {
        "c": conversation,
        "channel_label": CHANNEL_LABELS.get(Channel(conversation.channel), conversation.channel),
        "order_label": f"Pedido {order.olist_number}" if order and order.olist_number else f"Pedido {conversation.pack_ref}",
        "products": items,
        "shipment": shipment_text(shipment) if shipment else None,
        "messages": [{"seller": m.from_seller, "text": m.text, "at": _when(m.sent_at)} for m in recent],
        "last_at": _when(conversation.last_message_at),
        "blocked": blocked_text(conversation.blocked_reason) if conversation.remote_status == "blocked" else None,
    }


def _claim_view(claim: Claim) -> dict:
    now = utcnow()
    mine = claim.action_responsible == "seller" or any(a.get("mandatory") for a in claim.seller_actions or [])
    return {
        "claim": claim,
        "stage": CLAIM_STAGES.get(claim.stage or "", "reclamação").capitalize(),
        "reason": REASON_LABELS.get((claim.reason_id or "")[:3], None),
        "mine": mine,
        "due": _when(claim.due_date),
        "urgent": bool(mine and claim.due_date and (claim.due_date - now).total_seconds() < 24 * 3600),
        "opened": _when(claim.opened_at),
    }


@router.get("")
def postsale_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    claims = [_claim_view(c) for c in db.scalars(
        select(Claim).where(Claim.status == "opened").order_by(Claim.due_date.is_(None), Claim.due_date))]

    queue, handled = [], set()
    for action in db.scalars(select(Action).where(
            Action.type == ActionType.SEND_MESSAGE,
            Action.status.in_([ActionStatus.PENDING, ActionStatus.FAILED])).order_by(Action.created_at)):
        conversation = db.get(Conversation, int(action.target_id))
        if conversation is not None and conversation.status in WAITING:
            queue.append({**_conversation_view(db, conversation), "action": action})
            handled.add(conversation.id)
    unhandled = [_conversation_view(db, c) for c in db.scalars(
        select(Conversation).where(Conversation.status.in_(WAITING)).order_by(Conversation.last_message_at))
        if c.id not in handled]

    start, end = local_day_bounds_utc(local_today())
    answered = []
    for action in db.scalars(select(Action).where(
            Action.type == ActionType.SEND_MESSAGE, Action.executed_at >= start, Action.executed_at < end,
            Action.status.in_([ActionStatus.EXECUTED, ActionStatus.SIMULATED]))
            .order_by(Action.executed_at.desc()).limit(50)):
        conversation = db.get(Conversation, int(action.target_id))
        if conversation is not None:
            answered.append({"text": action.payload.get("text"), "at": to_local(action.executed_at).strftime("%H:%M"),
                             "simulated": action.status == ActionStatus.SIMULATED,
                             "order_label": _conversation_view(db, conversation)["order_label"]})
    return render(request, "postsale.html", {
        "claims": claims, "queue": queue, "unhandled": unhandled, "answered": answered,
    }, db=db)


def _clean_text(text: str, max_chars: int) -> str | None:
    text = " ".join((text or "").split())  # o Mercado Livre não aceita quebras de linha CRLF
    return text if 0 < len(text) <= max_chars else None


def _result_flash(request: Request, action: Action) -> None:
    if action.status == ActionStatus.SIMULATED:
        flash(request, "Mensagem aprovada. Modo simulação: nada foi enviado ao comprador.")
    elif action.status == ActionStatus.EXECUTED:
        flash(request, action.result or "Mensagem enviada ao comprador.")
    else:
        flash(request, f"A mensagem não foi enviada: {action.result}", "erro")


@router.post("/acao/{action_id}/enviar", dependencies=[Depends(verify_csrf)])
def send_suggestion(request: Request, action_id: int, text: str = Form(""), db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    """Aprovar a sugestão (ou enviar o texto editado/escrito por você)."""
    action = db.get(Action, action_id)
    conversation = db.get(Conversation, int(action.target_id)) if action else None
    if action is None or action.type != ActionType.SEND_MESSAGE or conversation is None:
        flash(request, "Mensagem não encontrada.", "erro")
        return RedirectResponse("/pos-venda", status_code=303)
    clean = _clean_text(text, conversation.max_chars)
    if clean is None:
        flash(request, f"Escreva a mensagem (até {conversation.max_chars} caracteres).", "erro")
    else:
        try:
            actions.approve(db, action, actor=f"user:{user.id}", edited_payload={"text": clean})
            db.commit()
            _result_flash(request, action)
        except actions.ActionNotAllowed as exc:
            flash(request, str(exc), "erro")
    return RedirectResponse("/pos-venda", status_code=303)


@router.post("/acao/{action_id}/ignorar", dependencies=[Depends(verify_csrf)])
def ignore_suggestion(request: Request, action_id: int, db: Session = Depends(get_db),
                      user: User = Depends(current_user)):
    """Tira da fila sem enviar (ex.: vou responder direto no Mercado Livre)."""
    action = db.get(Action, action_id)
    if action is not None and action.type == ActionType.SEND_MESSAGE:
        try:
            actions.reject(db, action, actor=f"user:{user.id}", reason="Ignorada no painel")
            conversation = db.get(Conversation, int(action.target_id))
            if conversation is not None and conversation.status in WAITING:
                conversation.status = ConversationStatus.CLOSED
            db.commit()
            flash(request, "Conversa tirada da fila. Nada foi enviado.")
        except actions.ActionNotAllowed as exc:
            flash(request, str(exc), "erro")
    return RedirectResponse("/pos-venda", status_code=303)


@router.post("/{conversation_id}/responder", dependencies=[Depends(verify_csrf)])
def answer_myself(request: Request, conversation_id: int, text: str = Form(""), db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    """Responder uma conversa que ainda não tem sugestão do agente."""
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.status not in WAITING:
        flash(request, "Conversa não encontrada.", "erro")
        return RedirectResponse("/pos-venda", status_code=303)
    clean = _clean_text(text, conversation.max_chars)
    if clean is None:
        flash(request, f"Escreva a mensagem (até {conversation.max_chars} caracteres).", "erro")
    else:
        action = actions.propose(db, agent_slug=None, type=ActionType.SEND_MESSAGE, channel=conversation.channel,
                                 target_type="conversa", target_id=conversation.id, payload={"text": clean},
                                 reason="Escrita por uma pessoa no painel")
        actions.approve(db, action, actor=f"user:{user.id}")
        db.commit()
        _result_flash(request, action)
    return RedirectResponse("/pos-venda", status_code=303)



@router.get("/avaliacoes")
def reviews_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Avaliações e devoluções por produto (90 dias) — só leitura."""
    from app.agents.config import has_real_data
    from app.services.quality import product_quality

    rows = product_quality(db, demo=not has_real_data(db))
    claims = sum(q.claims for q in rows)
    return render(request, "reviews.html", {
        "rows": rows[:60], "claims": claims, "low_reviews": sum(len(q.low_reviews) for q in rows),
        "with_insight": sum(1 for q in rows if q.insight), "products": len(rows),
    }, db=db)
