"""Agente 1 — Respondedor de perguntas (Mercado Livre; Shopee quando conectada).

Para cada pergunta nova:
1. Regras fixas primeiro (sem gastar IA): desconto/negociação → vai para uma pessoa.
2. Monta o contexto mínimo: ficha do anúncio, estoque do Olist e regras da loja.
3. Pede ao Haiku uma resposta curta em JSON, com confiança e assunto.
4. Confiança baixa, assunto delicado ou dado de contato na resposta → pessoa.
5. Cria uma Ação "aguardando" no ActionGateway. Nada é enviado aqui: só depois
   da aprovação no painel (ou, no modo automático, se a confiança for alta).
"""

import hashlib
import logging
import re
import unicodedata
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.base import run_agent
from app.ai.client import AIClient, AIError, AINotConfigured, AIRefused, BudgetExceeded, Transport
from app.core import actions
from app.core.db import utcnow
from app.core.money import format_brl
from app.core.timeutil import local_day_bounds_utc, local_today
from app.integrations.errors import IntegrationError, NotConnected
from app.integrations.mercadolivre.auth import MLTokenProvider, get_ml_account
from app.integrations.mercadolivre.client import MLClient
from app.integrations.olist.dates import to_cents
from app.models import Action, Product, Question, StockSnapshot
from app.models.enums import ActionStatus, ActionType, Channel, QuestionStatus
from app.services.store_settings import get_setting

logger = logging.getLogger("agentes.perguntas")

SLUG = "perguntas"
MAX_PER_RUN = 15
MAX_DESCRIPTION_CHARS = 1200
MAX_ATTRIBUTES = 30
PROMPT = (Path(__file__).resolve().parent.parent / "ai" / "prompts" / "responder_pergunta.md").read_text("utf-8")

SENSITIVE_TOPICS = {"desconto", "negociacao", "fora_da_ficha", "reclamacao"}
TOPICS = ["produto", "envio", "garantia", "estoque", "desconto", "negociacao", "fora_da_ficha", "reclamacao", "outro"]

# Pedidos de desconto/negociação: nem chama a IA, vai direto para uma pessoa.
NEGOTIATION_RE = re.compile(
    r"(desconto|descontinho|negoci|faz por|fazer por|faria por|deixa por|sai por|consegue por|"
    r"menor pre[cç]o|melhor pre[cç]o|abaixa|abaixar|baixar o pre|pechinch|cupom|brinde)", re.IGNORECASE)
# Dados de contato não podem ir numa resposta (regra do marketplace).
CONTACT_RE = re.compile(
    r"(\(?\d{2}\)?\s?9?\d{4}-?\d{4}|[\w.+-]+@[\w-]+\.[\w.]+|https?://|www\.|whats|zap\b|instagram|telegram)",
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

MLClientFactory = Callable[[], MLClient]


def normalize(text: str) -> str:
    """Texto em minúsculas, sem acentos e pontuação — para reconhecer perguntas repetidas."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def system_prompt(db: Session, channel_label: str, store_name: str) -> str:
    return PROMPT.format(
        loja=store_name,
        canal=channel_label,
        tom=get_setting(db, "answer_tone"),
        prazo=get_setting(db, "shipping_days"),
        garantia=get_setting(db, "warranty"),
    )


def listing_facts(db: Session, item: dict, description: str) -> str:
    """Só o necessário da ficha (contexto curto = custo baixo)."""
    lines = [f"ANÚNCIO: {item.get('title', '')}"]
    if item.get("price") is not None:
        lines.append(f"Preço: {format_brl(to_cents(item['price']))}")
    if item.get("condition"):
        lines.append(f"Condição: {'novo' if item['condition'] == 'new' else 'usado'}")
    shipping = item.get("shipping") or {}
    if "free_shipping" in shipping:
        lines.append(f"Frete grátis: {'sim' if shipping['free_shipping'] else 'não'}")
    if item.get("warranty"):
        lines.append(f"Garantia informada no anúncio: {item['warranty']}")
    attrs = [a for a in item.get("attributes") or [] if a.get("value_name") and a.get("id") != "SELLER_SKU"]
    if attrs:
        lines.append("Ficha técnica:")
        lines += [f"- {a.get('name')}: {a.get('value_name')}" for a in attrs[:MAX_ATTRIBUTES]]
    if description:
        lines.append(f"Descrição (trecho): {description[:MAX_DESCRIPTION_CHARS]}")
    lines.append(f"ESTOQUE: {stock_text(db, item)}")
    return "\n".join(lines)


def item_sku(item: dict) -> str | None:
    for attr in item.get("attributes") or []:
        if attr.get("id") == "SELLER_SKU" and attr.get("value_name"):
            return attr["value_name"]
    return item.get("seller_custom_field") or None


def stock_text(db: Session, item: dict) -> str:
    """Estoque vem do Olist (fonte da verdade); sem vínculo, usa o do anúncio."""
    sku = item_sku(item)
    product = db.scalar(select(Product).where(Product.sku == sku, Product.is_demo.is_(False))) if sku else None
    if product is not None:
        snap = db.scalar(select(StockSnapshot).where(StockSnapshot.product_id == product.id)
                         .order_by(StockSnapshot.captured_at.desc(), StockSnapshot.id.desc()).limit(1))
        if snap is not None:
            return "disponível" if snap.quantity - snap.reserved > 0 else "esgotado no momento"
    available = item.get("available_quantity")
    if available is None:
        return "sem informação"
    return "disponível" if int(available) > 0 else "esgotado no momento"


def already_handled(db: Session, question: Question) -> bool:
    return db.scalar(select(func.count()).select_from(Action).where(
        Action.type == ActionType.ANSWER_QUESTION, Action.target_id == str(question.id),
        Action.status != ActionStatus.REJECTED)) > 0


def process_question(db: Session, question: Question, ml: MLClient, ai: AIClient, store_name: str) -> Action:
    """Gera a sugestão de resposta e cria a Ação. Erros de IA/API sobem para quem chamou."""
    base = {"agent_slug": SLUG, "type": ActionType.ANSWER_QUESTION, "channel": question.channel,
            "target_type": "pergunta", "target_id": question.id}

    if NEGOTIATION_RE.search(question.text or ""):
        question.status = QuestionStatus.HUMAN
        return actions.propose(db, **base, payload={"text": ""}, confidence="baixa", topic="negociacao",
                               reason="Pedido de desconto ou negociação: precisa de uma pessoa.", needs_human=True)

    item = ml.get_item(question.listing_external_id) if question.listing_external_id else None
    if not item:
        raise IntegrationError("Não foi possível ler o anúncio desta pergunta.")
    description = ml.get_item_description(question.listing_external_id)
    facts = listing_facts(db, item, description)
    question.listing_title = item.get("title") or question.listing_title

    try:
        result = ai.complete_json(
            task="responder_pergunta", agent_slug=SLUG,
            system=system_prompt(db, "Mercado Livre", store_name),
            user=f"{facts}\n\nPERGUNTA DO COMPRADOR: \"{question.text}\"",
            schema=ANSWER_SCHEMA,
            cache_key_parts=[question.listing_external_id, hashlib.sha256(facts.encode()).hexdigest(),
                             normalize(question.text or "")],
            max_tokens=1024,
        )
    except AIRefused as exc:
        question.status = QuestionStatus.HUMAN
        return actions.propose(db, **base, payload={"text": ""}, confidence="baixa", topic="outro",
                               reason=str(exc), needs_human=True)

    text = (result.get("resposta") or "").strip()
    confidence, topic = result.get("confianca", "baixa"), result.get("assunto", "outro")
    reasons = []
    if confidence == "baixa":
        reasons.append("A IA não tem certeza")
    if topic in SENSITIVE_TOPICS:
        reasons.append(f"Assunto delicado ({topic.replace('_', ' ')})")
    if CONTACT_RE.search(text):
        reasons.append("A sugestão tinha dado de contato (proibido)")
        text = ""
    if not text:
        reasons.append("Sem sugestão de resposta")
    needs_human = bool(reasons)
    reason = "; ".join(reasons) + (f". {result.get('motivo', '')}" if result.get("motivo") else "") \
        if needs_human else result.get("motivo")

    question.status = QuestionStatus.HUMAN if needs_human else QuestionStatus.PROPOSED
    return actions.propose(db, **base, payload={"text": text}, confidence=confidence, topic=topic,
                           reason=reason, needs_human=needs_human, auto_ok=confidence == "alta")


def today_summary(db: Session) -> str:
    start, end = local_day_bounds_utc(local_today())
    answered = db.scalar(select(func.count()).select_from(Question).where(
        Question.answered_at >= start, Question.answered_at < end,
        Question.status.in_([QuestionStatus.ANSWERED, QuestionStatus.SIMULATED])))
    waiting = db.scalar(select(func.count()).select_from(Action).where(
        Action.type == ActionType.ANSWER_QUESTION,
        Action.status.in_([ActionStatus.PENDING, ActionStatus.FAILED])))
    return f"{answered} respondidas hoje · {waiting} aguardando você"


def run_questions_agent(session_factory: sessionmaker[Session], *, transport: Transport | None = None,
                        ml_client_factory: MLClientFactory | None = None):
    """Uma rodada do agente (chamada pelo worker a cada AGENT_QUESTIONS_MINUTES)."""

    def work(db: Session, _agent) -> tuple[int, str]:
        account = get_ml_account(db)
        if account is None or not account.access_token_enc:
            return 0, "Aguardando o Mercado Livre ser conectado"
        pending = list(db.scalars(
            select(Question).where(Question.channel == Channel.ML, Question.status == QuestionStatus.NEW,
                                   Question.is_demo.is_(False),
                                   Question.asked_at > utcnow() - timedelta(days=30))
            .order_by(Question.asked_at).limit(MAX_PER_RUN)))
        if not pending:
            return 0, today_summary(db)

        ai = AIClient(db, transport)
        store_name = get_setting(db, "store_name") or account.account_name or "nossa loja"
        factory = ml_client_factory or (lambda: MLClient(MLTokenProvider(session_factory)))
        done = 0
        with factory() as ml:
            for question in pending:
                if already_handled(db, question):
                    continue
                try:
                    process_question(db, question, ml, ai, store_name)
                    done += 1
                    db.commit()
                except (AINotConfigured, BudgetExceeded) as exc:
                    db.commit()  # guarda o que já foi feito e o registro de uso
                    return done, str(exc)
                except (AIError, IntegrationError, NotConnected) as exc:
                    db.rollback()
                    logger.warning("Pergunta %s ficou para a próxima rodada: %s", question.id, exc)
        return done, today_summary(db)

    with session_factory() as db:
        return run_agent(db, SLUG, work)
