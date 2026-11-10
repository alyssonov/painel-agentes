"""Agente 5 — Pós-venda (Mercado Livre; Shopee quando conectada).

Para cada conversa em que a última mensagem é do comprador:
1. Regras fixas primeiro (sem gastar IA): reclamação aberta, pedido de troca,
   devolução, cancelamento, reembolso ou produto com defeito → vai para uma pessoa.
2. Monta o contexto mínimo: itens do pedido, situação do envio e da nota (Olist),
   regras da loja e só as últimas mensagens.
3. Pede ao Haiku uma resposta curta em JSON, com confiança e assunto.
4. Assunto delicado, confiança baixa ou dado de contato na resposta → pessoa.
5. Cria uma Ação "aguardando" no ActionGateway. Nada é enviado aqui.

Reclamações: este agente só AVISA (faixa "O que pede atenção"). Responder ou
resolver uma reclamação continua sendo feito no Mercado Livre.
"""

import hashlib
import logging
import re
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.base import run_agent
from app.agents.config import has_real_data
from app.agents.questions import CONTACT_RE, normalize
from app.ai.client import AIClient, AIError, AINotConfigured, AIRefused, BudgetExceeded, Transport
from app.core import actions
from app.core.alerts import AlertSpec, sync_alerts
from app.core.db import utcnow
from app.core.timeutil import local_day_bounds_utc, local_today, to_local
from app.integrations.mercadolivre.auth import get_ml_account
from app.integrations.mercadolivre.mapping import left_the_store
from app.models import Action, Claim, Conversation, Order, Shipment
from app.models.enums import ActionStatus, ActionType, AlertSeverity, Channel, ConversationStatus
from app.services.store_settings import get_setting

logger = logging.getLogger("agentes.posvenda")

SLUG = "posvenda"
MAX_PER_RUN = 15
LAST_MESSAGES = 6
MAX_ANSWER_CHARS = 300
PROMPT = (Path(__file__).resolve().parent.parent / "ai" / "prompts" / "responder_mensagem.md").read_text("utf-8")

TOPICS = ["rastreio", "prazo_entrega", "nota_fiscal", "produto", "endereco", "agradecimento",
          "troca_devolucao", "cancelamento", "reclamacao", "outro"]
SENSITIVE_TOPICS = {"troca_devolucao", "cancelamento", "reclamacao"}

# Assuntos que sempre vão para uma pessoa, sem gastar IA.
SENSITIVE_RE = re.compile(
    r"(cancel|devolu|devolv|troca|trocar|reembols|estorno|dinheiro de volta|defeit|quebrad|danific|"
    r"amassad|errad|faltando|falta (uma|um|o|a)|n[aã]o (chegou|recebi|veio)|procon|reclama|golpe|advogad)",
    re.IGNORECASE)

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "resposta": {"type": "string"},
        "confianca": {"type": "string", "enum": ["alta", "media", "baixa"]},
        "assunto": {"type": "string", "enum": TOPICS},
        "motivo": {"type": "string"},
    },
    "required": ["resposta", "confianca", "assunto", "motivo"],
    "additionalProperties": False,
}

# Motivos de bloqueio do Mercado Livre em português simples (página "Mensagens bloqueadas").
BLOCKED_REASONS = {
    "blocked_by_time": "passaram 30 dias da última mensagem",
    "blocked_by_buyer": "o comprador bloqueou as mensagens",
    "blocked_by_mediation": "há uma mediação em andamento",
    "blocked_by_mediation_fbm": "há uma mediação em andamento",
    "blocked_by_fulfillment": "venda Full: libera quando o pacote for entregue",
    "blocked_by_payment": "o pagamento ainda não foi aprovado",
    "blocked_by_cancelled_order": "a venda foi cancelada",
    "blocked_by_refund": "houve reembolso",
    "blocked_by_message_pending_review": "a conversa está em revisão pelo Mercado Livre",
}

CLAIM_STAGES = {"claim": "reclamação", "dispute": "mediação com o Mercado Livre", "recontact": "recontato"}


def blocked_text(reason: str | None) -> str:
    return BLOCKED_REASONS.get(reason or "", "o Mercado Livre não permite responder agora")


def shipment_text(row: Shipment | None) -> str:
    """Situação do envio em português simples, para a IA e para o painel."""
    if row is None:
        return "sem informação do envio"
    if row.status == "delivered":
        return "entregue"
    if row.substatus == "out_for_delivery":
        return "saiu para entrega"
    if row.status == "shipped":
        return "a caminho (já despachado)"
    if left_the_store(row.status, row.substatus):
        when = f" em {to_local(row.shipped_at):%d/%m}" if row.shipped_at else ""
        return f"já despachado{when}, a caminho do centro de distribuição"
    if row.status in ("ready_to_ship", "handling"):
        when = f" até {to_local(row.dispatch_deadline):%d/%m}" if row.dispatch_deadline else ""
        return f"em preparação, será despachado{when}"
    if row.status == "pending":
        return "aguardando liberação do envio"
    if row.status == "cancelled":
        return "envio cancelado"
    return "sem informação do envio"


def order_for(db: Session, conversation: Conversation) -> Order | None:
    return db.scalar(select(Order).where(Order.channel == conversation.channel,
                                         Order.external_id == conversation.pack_ref))


def open_claim_for(db: Session, conversation: Conversation) -> Claim | None:
    refs = [conversation.pack_ref]
    query = select(Claim).where(Claim.channel == conversation.channel, Claim.status == "opened")
    if conversation.claim_ref:
        claim = db.scalar(query.where(Claim.external_id == conversation.claim_ref))
        if claim:
            return claim
    return db.scalar(query.where(Claim.order_ref.in_(refs)))


def order_facts(db: Session, conversation: Conversation) -> str:
    """Só o necessário do pedido (contexto curto = custo baixo). Nada de nome, endereço ou CPF."""
    order = order_for(db, conversation)
    lines = []
    if order is not None:
        lines.append(f"PEDIDO feito em {to_local(order.ordered_at):%d/%m/%Y}")
        for item in order.items[:10]:
            lines.append(f"- {item.quantity} × {item.title}")
        lines.append(f"Nota fiscal: {'emitida' if order.invoice_olist_id else 'ainda não emitida'}")
    else:
        lines.append("PEDIDO: dados ainda não importados do Olist")
    shipment = db.scalar(select(Shipment).where(Shipment.channel == conversation.channel,
                                                Shipment.order_ref == conversation.pack_ref))
    lines.append(f"ENVIO: {shipment_text(shipment)}")
    if shipment is not None and shipment.logistic_type == "fulfillment":
        lines.append("Envio pelo Full (o Mercado Livre despacha).")
    return "\n".join(lines)


def conversation_text(conversation: Conversation) -> str:
    recent = sorted(conversation.messages, key=lambda m: m.sent_at)[-LAST_MESSAGES:]
    return "\n".join(f"{'LOJA' if m.from_seller else 'COMPRADOR'}: {m.text[:600]}" for m in recent)


def last_buyer_text(conversation: Conversation) -> str:
    for message in sorted(conversation.messages, key=lambda m: m.sent_at, reverse=True):
        if not message.from_seller:
            return message.text
    return ""


def system_prompt(db: Session, store_name: str) -> str:
    return PROMPT.format(loja=store_name, canal="Mercado Livre", tom=get_setting(db, "answer_tone"),
                         prazo=get_setting(db, "shipping_days"), garantia=get_setting(db, "warranty"))


def already_handled(db: Session, conversation: Conversation) -> bool:
    return db.scalar(select(func.count()).select_from(Action).where(
        Action.type == ActionType.SEND_MESSAGE, Action.target_id == str(conversation.id),
        Action.status.in_([ActionStatus.PENDING, ActionStatus.FAILED]))) > 0


def process_conversation(db: Session, conversation: Conversation, ai: AIClient, store_name: str) -> Action:
    """Gera a sugestão de resposta e cria a Ação. Erros de IA sobem para quem chamou."""
    base = {"agent_slug": SLUG, "type": ActionType.SEND_MESSAGE, "channel": conversation.channel,
            "target_type": "conversa", "target_id": conversation.id}
    buyer_text = last_buyer_text(conversation)

    claim = open_claim_for(db, conversation)
    if claim is not None:
        conversation.status = ConversationStatus.HUMAN
        stage = CLAIM_STAGES.get(claim.stage or "", "reclamação")
        return actions.propose(db, **base, payload={"text": ""}, confidence="baixa", topic="reclamacao",
                               reason=f"Este pedido tem uma {stage} aberta: responda pelo Mercado Livre.",
                               needs_human=True)
    if SENSITIVE_RE.search(buyer_text):
        conversation.status = ConversationStatus.HUMAN
        return actions.propose(db, **base, payload={"text": ""}, confidence="baixa", topic="troca_devolucao",
                               reason="Fala de problema, troca, devolução ou cancelamento: precisa de uma pessoa.",
                               needs_human=True)

    facts = order_facts(db, conversation)
    history = conversation_text(conversation)
    try:
        result = ai.complete_json(
            task="responder_mensagem", agent_slug=SLUG, system=system_prompt(db, store_name),
            user=f"{facts}\n\nCONVERSA (mais antiga primeiro):\n{history}\n\nEscreva a resposta da LOJA.",
            schema=ANSWER_SCHEMA,
            cache_key_parts=[hashlib.sha256(f"{facts}\n{history}".encode()).hexdigest(), normalize(buyer_text)],
            max_tokens=1024,
        )
    except AIRefused as exc:
        conversation.status = ConversationStatus.HUMAN
        return actions.propose(db, **base, payload={"text": ""}, confidence="baixa", topic="outro",
                               reason=str(exc), needs_human=True)

    text = " ".join((result.get("resposta") or "").split())
    confidence, topic = result.get("confianca", "baixa"), result.get("assunto", "outro")
    reasons = []
    if confidence == "baixa":
        reasons.append("A IA não tem certeza")
    if topic in SENSITIVE_TOPICS:
        reasons.append(f"Assunto delicado ({topic.replace('_', ' ')})")
    if CONTACT_RE.search(text):
        reasons.append("A sugestão tinha dado de contato (proibido)")
        text = ""
    if len(text) > min(conversation.max_chars, MAX_ANSWER_CHARS + 50):
        reasons.append("A sugestão ficou longa demais")
        text = text[:conversation.max_chars]
    if not text:
        reasons.append("Sem sugestão de resposta")
    needs_human = bool(reasons)
    reason = "; ".join(reasons) + (f". {result.get('motivo', '')}" if result.get("motivo") else "") \
        if needs_human else result.get("motivo")
    conversation.status = ConversationStatus.HUMAN if needs_human else ConversationStatus.PROPOSED
    return actions.propose(db, **base, payload={"text": text}, confidence=confidence, topic=topic,
                           reason=reason, needs_human=needs_human, auto_ok=confidence == "alta")


# --- Reclamações: só avisos ---------------------------------------------------------

def claim_alerts(db: Session) -> list[AlertSpec]:
    specs = []
    now = utcnow()
    for claim in db.scalars(select(Claim).where(Claim.status == "opened", Claim.is_demo.is_(False))
                            .order_by(Claim.due_date)):
        mine = claim.action_responsible == "seller" or any(a.get("mandatory") for a in claim.seller_actions or [])
        stage = CLAIM_STAGES.get(claim.stage or "", "reclamação")
        title = f"{stage.capitalize()} aberta no pedido {claim.order_ref or claim.external_id} (Mercado Livre)"
        detail = claim.problem or claim.title or "Veja no Mercado Livre."
        if mine and claim.due_date:
            detail += f" Responda até {to_local(claim.due_date):%d/%m às %H:%M}."
        if claim.affects_reputation == "affected":
            detail += " Afeta a reputação."
        late = claim.due_date is not None and claim.due_date - now < timedelta(hours=24)
        severity = AlertSeverity.CRITICAL if mine and (late or claim.affects_reputation == "affected") \
            else AlertSeverity.WARNING if mine else AlertSeverity.INFO
        specs.append(AlertSpec(f"reclamacao:{claim.external_id}", severity, title, detail, "reclamacao"))
    return specs[:10]


def today_summary(db: Session) -> str:
    start, end = local_day_bounds_utc(local_today())
    answered = db.scalar(select(func.count()).select_from(Action).where(
        Action.type == ActionType.SEND_MESSAGE, Action.executed_at >= start, Action.executed_at < end,
        Action.status.in_([ActionStatus.EXECUTED, ActionStatus.SIMULATED])))
    waiting = db.scalar(select(func.count()).select_from(Conversation).where(
        Conversation.status.in_([ConversationStatus.NEW, ConversationStatus.PROPOSED, ConversationStatus.HUMAN])))
    claims = db.scalar(select(func.count()).select_from(Claim).where(Claim.status == "opened"))
    parts = [f"{answered} respondida(s) hoje", f"{waiting} aguardando você"]
    if claims:
        parts.append(f"{claims} reclamação(ões) aberta(s)")
    return " · ".join(parts)


def run_postsale_agent(session_factory: sessionmaker[Session], *, transport: Transport | None = None):
    """Uma rodada do agente (chamada pelo worker)."""

    def work(db: Session, _agent) -> tuple[int, str]:
        account = get_ml_account(db)
        if account is None or not account.access_token_enc or not has_real_data(db):
            return 0, "Aguardando o Mercado Livre ser conectado"
        sync_alerts(db, SLUG, claim_alerts(db))
        db.commit()
        pending = [c for c in db.scalars(
            select(Conversation).where(Conversation.channel == Channel.ML, Conversation.is_demo.is_(False),
                                       Conversation.status == ConversationStatus.NEW,
                                       Conversation.last_message_at > utcnow() - timedelta(days=30))
            .order_by(Conversation.last_message_at).limit(MAX_PER_RUN))]
        if not pending:
            return 0, today_summary(db)

        ai = AIClient(db, transport)
        store_name = get_setting(db, "store_name") or account.account_name or "nossa loja"
        done = 0
        for conversation in pending:
            if already_handled(db, conversation):
                continue
            try:
                process_conversation(db, conversation, ai, store_name)
                done += 1
                db.commit()
            except (AINotConfigured, BudgetExceeded) as exc:
                db.commit()
                return done, str(exc)
            except AIError as exc:
                db.rollback()
                logger.warning("Conversa %s ficou para a próxima rodada: %s", conversation.id, exc)
        return done, today_summary(db)

    with session_factory() as db:
        return run_agent(db, SLUG, work)
