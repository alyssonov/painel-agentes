"""ActionGateway — o ÚNICO caminho para qualquer escrita fora do painel.

Regras (CLAUDE.md):
- toda ação que fala com cliente ou muda preço/anúncio nasce como "aguardando"
  e só é executada depois de aprovada por uma pessoa;
- no modo automático, o agente só aprova sozinho o que passa nas regras dele;
- com DRY_RUN=true nada é enviado de verdade: a ação vira "simulada";
- tudo é registrado no audit_log (o quê, quando, quem aprovou, resultado).
"""

import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import audit
from app.core.db import get_sessionmaker, utcnow
from app.integrations.errors import IntegrationError
from app.models import Action, Agent, Conversation, Listing, PriceReference, Question
from app.models.enums import ActionStatus, ActionType, AgentMode, Channel, ConversationStatus, QuestionStatus

logger = logging.getLogger("acoes")


class ActionNotAllowed(Exception):
    """A ação não pode ser aprovada/executada no estado atual."""


# --- Executores: um por tipo de ação. Os módulos de escrita só são importados aqui. ---

def _execute_answer_question(db: Session, action: Action) -> str:
    question = db.get(Question, int(action.target_id))
    if question is None:
        raise IntegrationError("A pergunta não existe mais.")
    if question.channel != Channel.ML:
        raise IntegrationError("Responder perguntas da Shopee ainda não está disponível.")
    from app.integrations.mercadolivre.auth import MLTokenProvider
    from app.integrations.mercadolivre.writes import MLWriter

    with MLWriter(MLTokenProvider(get_sessionmaker())) as writer:
        writer.answer_question(question.external_id, action.payload["text"])
    return "Resposta publicada no Mercado Livre."


def _execute_send_message(db: Session, action: Action) -> str:
    conversation = db.get(Conversation, int(action.target_id))
    if conversation is None:
        raise IntegrationError("A conversa não existe mais.")
    if conversation.channel != Channel.ML:
        raise IntegrationError("Mensagens da Shopee ainda não estão disponíveis.")
    if conversation.remote_status == "blocked":
        raise IntegrationError("O Mercado Livre bloqueou esta conversa. Veja o pedido no Mercado Livre.")
    from app.integrations.mercadolivre.auth import MLTokenProvider, get_ml_account
    from app.integrations.mercadolivre.writes import MLWriter

    account = get_ml_account(db)
    if account is None or not account.external_id:
        raise IntegrationError("O Mercado Livre não está conectado.")
    with MLWriter(MLTokenProvider(get_sessionmaker())) as writer:
        result = writer.send_message(conversation.pack_ref, account.external_id, action.payload["text"],
                                     conversation.max_chars)
    if str(result.get("status") or "").lower() == "pending_review":
        return "Mensagem enviada. O Mercado Livre está revisando antes de entregar ao comprador."
    return "Mensagem enviada ao comprador."


def _execute_update_price(db: Session, action: Action) -> str:
    ref = db.get(PriceReference, int(action.target_id))
    if ref is None or ref.channel != Channel.ML:
        raise IntegrationError("O anúncio não foi encontrado.")
    if ref.automated:
        raise IntegrationError("A automatização de preços do Mercado Livre está ligada neste anúncio.")
    if ref.has_variations or ref.promoted:
        raise IntegrationError("Anúncio com variações ou em promoção: ajuste o preço no Mercado Livre.")
    from app.integrations.mercadolivre.auth import MLTokenProvider
    from app.integrations.mercadolivre.writes import MLWriter

    with MLWriter(MLTokenProvider(get_sessionmaker())) as writer:
        writer.update_price(ref.item_id, int(action.payload["price_cents"]))
    return "Preço alterado no Mercado Livre."


EXECUTORS: dict[str, Callable[[Session, Action], str]] = {
    ActionType.ANSWER_QUESTION: _execute_answer_question,
    ActionType.SEND_MESSAGE: _execute_send_message,
    ActionType.UPDATE_PRICE: _execute_update_price,
}


def _after_answer(db: Session, action: Action) -> None:
    """Atualiza a pergunta depois que a resposta saiu (ou foi simulada)."""
    question = db.get(Question, int(action.target_id))
    if question is None:
        return
    question.answer_text = action.payload.get("text")
    question.answered_at = utcnow()
    question.status = QuestionStatus.SIMULATED if action.status == ActionStatus.SIMULATED else QuestionStatus.ANSWERED


def _after_message(db: Session, action: Action) -> None:
    """Conversa sai da fila depois que a mensagem saiu (ou foi simulada)."""
    conversation = db.get(Conversation, int(action.target_id))
    if conversation is None:
        return
    simulated = action.status == ActionStatus.SIMULATED
    conversation.status = ConversationStatus.SIMULATED if simulated else ConversationStatus.ANSWERED
    if not simulated:
        conversation.last_from_buyer = False


def _after_price(db: Session, action: Action) -> None:
    ref = db.get(PriceReference, int(action.target_id))
    if ref is None:
        return
    if action.status == ActionStatus.SIMULATED:
        ref.verdict = "simulado"
        return
    new = int(action.payload["price_cents"])
    ref.verdict, ref.current_cents, ref.listed_cents = "aplicado", new, new
    for listing in db.scalars(select(Listing).where(Listing.channel == ref.channel, Listing.item_id == ref.item_id)):
        listing.price_cents = new


AFTER_SUCCESS: dict[str, Callable[[Session, Action], None]] = {
    ActionType.ANSWER_QUESTION: _after_answer,
    ActionType.SEND_MESSAGE: _after_message,
    ActionType.UPDATE_PRICE: _after_price,
}


# --- API do gateway -----------------------------------------------------------------

def propose(db: Session, *, agent_slug: str | None, type: str, channel: str | None, target_type: str,
            target_id: str | int, payload: dict, confidence: str | None = None, topic: str | None = None,
            reason: str | None = None, needs_human: bool = False, auto_ok: bool = False) -> Action:
    """Cria uma ação "aguardando". Se o agente está no automático e auto_ok, aprova e executa."""
    action = Action(agent_slug=agent_slug, type=type, channel=channel, target_type=target_type,
                    target_id=str(target_id), payload=dict(payload), original_payload=dict(payload),
                    confidence=confidence, topic=topic, reason=reason, needs_human=needs_human,
                    status=ActionStatus.PENDING, dry_run=get_settings().dry_run)
    db.add(action)
    db.flush()
    audit.record(db, actor=f"agent:{agent_slug}" if agent_slug else "system", event="acao.proposta",
                 target_type=target_type, target_id=target_id,
                 details={"acao": action.id, "tipo": type, "confianca": confidence, "humano": needs_human})
    if agent_slug and auto_ok and not needs_human:
        agent = db.scalar(select(Agent).where(Agent.slug == agent_slug))
        if agent is not None and agent.mode == AgentMode.AUTO:
            approve(db, action, actor=f"agent:{agent_slug}")
    return action


def approve(db: Session, action: Action, *, actor: str, edited_payload: dict | None = None) -> Action:
    """Aprova (opcionalmente com o texto editado) e executa na hora.

    Também serve para tentar de novo uma ação que falhou no envio.
    """
    if action.status not in (ActionStatus.PENDING, ActionStatus.FAILED):
        raise ActionNotAllowed("Esta ação já foi resolvida.")
    edited = edited_payload is not None and edited_payload != action.payload
    if edited_payload is not None:
        action.payload = dict(edited_payload)
    action.decided_by = actor
    action.decided_at = utcnow()
    audit.record(db, actor=actor, event="acao.aprovada_editada" if edited else "acao.aprovada",
                 target_type=action.target_type, target_id=action.target_id, details={"acao": action.id})
    return _execute(db, action)


def reject(db: Session, action: Action, *, actor: str, reason: str | None = None) -> Action:
    if action.status not in (ActionStatus.PENDING, ActionStatus.FAILED):
        raise ActionNotAllowed("Esta ação já foi resolvida.")
    action.status = ActionStatus.REJECTED
    action.decided_by = actor
    action.decided_at = utcnow()
    action.result = reason
    audit.record(db, actor=actor, event="acao.descartada", target_type=action.target_type,
                 target_id=action.target_id, details={"acao": action.id, "motivo": reason})
    return action


def _execute(db: Session, action: Action) -> Action:
    if get_settings().dry_run:
        # MODO SIMULAÇÃO: nada sai do painel.
        action.status = ActionStatus.SIMULATED
        action.executed_at = utcnow()
        action.result = "Modo simulação: nada foi enviado de verdade."
    else:
        executor = EXECUTORS.get(action.type)
        if executor is None:
            raise ActionNotAllowed(f"Tipo de ação desconhecido: {action.type}")
        try:
            action.result = executor(db, action)
            action.status = ActionStatus.EXECUTED
            action.executed_at = utcnow()
        except IntegrationError as exc:
            # Falhou: volta para a fila para alguém tentar de novo (nada foi enviado).
            action.status = ActionStatus.FAILED
            action.result = str(exc)
    if action.status in (ActionStatus.EXECUTED, ActionStatus.SIMULATED):
        hook = AFTER_SUCCESS.get(action.type)
        if hook:
            hook(db, action)
    audit.record(db, actor="system", event=f"acao.{action.status}", target_type=action.target_type,
                 target_id=action.target_id, details={"acao": action.id, "resultado": action.result})
    return action
