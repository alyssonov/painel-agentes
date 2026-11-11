"""Agente 2 — Anúncios (Mercado Livre).

1. Usa a QUALIDADE oficial de cada anúncio (/item/{id}/performance, lida 1×/semana).
2. Escolhe os anúncios fracos (nota abaixo do limite) que MAIS VENDEM — onde
   melhorar dá mais resultado — respeitando um teto de sugestões por semana.
3. Pede à IA (tarefa "melhorar_anuncio") uma descrição nova em texto simples e,
   só se o anúncio ainda não tem vendas, um título novo. A IA só usa a ficha
   técnica e os dados do anúncio: não inventa especificações.
4. Cria uma Ação "melhorar_anuncio" no ActionGateway. NUNCA é aprovada sozinha,
   nem no modo automático (regra do projeto: anúncio não publica sem aprovação).

O que a IA não resolve (fotos, código universal, ficha técnica) aparece no
painel com o link oficial do Mercado Livre para corrigir.
"""

import hashlib
import logging
import re
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.base import run_agent
from app.agents.config import agent_rules, has_real_data
from app.agents.questions import CONTACT_RE
from app.ai.client import AIClient, AIError, AINotConfigured, AIRefused, BudgetExceeded, Transport
from app.core import actions
from app.core.db import utcnow
from app.integrations.errors import IntegrationError, NotConnected
from app.integrations.mercadolivre.auth import MLTokenProvider, get_ml_account
from app.integrations.mercadolivre.client import MLClient
from app.models import Action, ListingQuality, Order, OrderItem, Product
from app.models.enums import ActionStatus, ActionType, Channel, KanbanStatus
from app.services.store_settings import get_setting

logger = logging.getLogger("agentes.anuncios")

SLUG = "anuncios"
MAX_PER_RUN = 3
COOLDOWN = timedelta(days=30)  # depois de aprovar/descartar, só volta a sugerir para o anúncio em 30 dias
MAX_TITLE = 60
MAX_DESCRIPTION = 3000
MAX_ATTRIBUTES = 40
PROMPT = (Path(__file__).resolve().parent.parent / "ai" / "prompts" / "melhorar_anuncio.md").read_text("utf-8")
_NO_EMOJI = re.compile(r"[\U00010000-\U0010FFFF<>]")

SCHEMA = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string"},
        "descricao": {"type": "string"},
        "o_que_mudou": {"type": "array", "items": {"type": "string"}},
        "confianca": {"type": "string", "enum": ["alta", "media", "baixa"]},
    },
    "required": ["titulo", "descricao", "o_que_mudou", "confianca"],
    "additionalProperties": False,
}

MLClientFactory = Callable[[], MLClient]


def weekly_used(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Action).where(
        Action.type == ActionType.UPDATE_LISTING, Action.agent_slug == SLUG,
        Action.created_at >= utcnow() - timedelta(days=7))) or 0


def candidates(db: Session, min_score: float) -> list[ListingQuality]:
    """Anúncios fracos, os que mais vendem primeiro, sem sugestão recente."""
    since = utcnow() - timedelta(days=30)
    units = dict(db.execute(
        select(OrderItem.product_id, func.sum(OrderItem.quantity)).join(Order, Order.id == OrderItem.order_id)
        .where(Order.ordered_at >= since, Order.kanban_status != KanbanStatus.CANCELLED, Order.is_demo.is_(False),
               OrderItem.product_id.is_not(None)).group_by(OrderItem.product_id)).all())
    weak = [q for q in db.scalars(select(ListingQuality).where(
        ListingQuality.channel == Channel.ML, ListingQuality.is_demo.is_(False),
        ListingQuality.score.is_not(None), ListingQuality.score < min_score))]
    recent = set(db.scalars(select(Action.target_id).where(
        Action.type == ActionType.UPDATE_LISTING,
        (Action.status.in_([ActionStatus.PENDING, ActionStatus.FAILED])) | (Action.created_at >= utcnow() - COOLDOWN))))
    weak = [q for q in weak if str(q.id) not in recent]
    weak.sort(key=lambda q: (-(units.get(q.product_id) or 0), q.score))
    return weak


def listing_context(item: dict, description: str, quality: ListingQuality, product: Product | None) -> str:
    lines = [f"TÍTULO ATUAL: {item.get('title') or quality.title}"]
    if product:
        lines.append(f"NOME DO PRODUTO NO SISTEMA: {product.name}")
    attrs = [a for a in item.get("attributes") or [] if a.get("value_name") and a.get("id") != "SELLER_SKU"]
    if attrs:
        lines.append("FICHA TÉCNICA:")
        lines += [f"- {a.get('name')}: {a.get('value_name')}" for a in attrs[:MAX_ATTRIBUTES]]
    lines.append(f"DESCRIÇÃO ATUAL: {(description or '(vazia)')[:2000]}")
    if quality.pending:
        lines.append("O QUE O MERCADO LIVRE DIZ QUE FALTA: " + "; ".join(p["label"] for p in quality.pending))
    lines.append(f"NOTA DE QUALIDADE: {quality.score:.0f}/100 ({quality.level_wording or quality.level})")
    return "\n".join(lines)


def system_prompt(db: Session, store_name: str, title_allowed: bool) -> str:
    rule = ("pode sugerir um título melhor (até 60 caracteres: produto + marca/modelo + característica principal, "
            "sem palavras repetidas nem CAIXA ALTA); se o atual já for bom, devolva vazio."
            if title_allowed else "o anúncio já tem vendas e o título NÃO pode mudar: devolva \"titulo\" vazio.")
    return PROMPT.format(loja=store_name, regra_titulo=rule, prazo=get_setting(db, "shipping_days"),
                         garantia=get_setting(db, "warranty"))


def process_listing(db: Session, quality: ListingQuality, ml: MLClient, ai: AIClient, store_name: str) -> Action | None:
    item = ml.get_item(quality.item_id)
    if not item:
        raise IntegrationError("Não foi possível ler o anúncio.")
    description = ml.get_item_description(quality.item_id)
    product = db.get(Product, quality.product_id) if quality.product_id else None
    title_allowed = int(item.get("sold_quantity") or 0) == 0
    context = listing_context(item, description, quality, product)
    try:
        result = ai.complete_json(task="melhorar_anuncio", agent_slug=SLUG,
                                  system=system_prompt(db, store_name, title_allowed), user=context, schema=SCHEMA,
                                  cache_key_parts=[hashlib.sha256(context.encode()).hexdigest()], max_tokens=3000)
    except AIRefused as exc:
        logger.warning("IA recusou o anúncio %s: %s", quality.item_id, exc)
        return None
    new_description = _NO_EMOJI.sub("", (result.get("descricao") or "").strip())[:MAX_DESCRIPTION]
    new_title = " ".join(_NO_EMOJI.sub("", result.get("titulo") or "").split()) if title_allowed else ""
    if len(new_title) > MAX_TITLE or new_title == (item.get("title") or "").strip():
        new_title = ""
    if not new_description or CONTACT_RE.search(new_description) or CONTACT_RE.search(new_title):
        logger.warning("Sugestão do anúncio %s descartada (vazia ou com dado de contato).", quality.item_id)
        return None
    changes = [str(c)[:200] for c in (result.get("o_que_mudou") or [])[:5]]
    return actions.propose(
        db, agent_slug=SLUG, type=ActionType.UPDATE_LISTING, channel=Channel.ML, target_type="anuncio_texto",
        target_id=quality.id,
        payload={"item_id": quality.item_id, "description": new_description, "title": new_title or None,
                 "old_title": item.get("title"), "old_description": (description or "")[:MAX_DESCRIPTION],
                 "changes": changes},
        confidence=result.get("confianca", "media"), topic="anuncio",
        reason="; ".join(changes) or "Descrição reorganizada com os dados da ficha técnica.",
        auto_ok=False)  # anúncio nunca é publicado sem aprovação


def run_listings_agent(session_factory: sessionmaker[Session], *, transport: Transport | None = None,
                       ml_client_factory: MLClientFactory | None = None):
    with session_factory() as db:
        if not has_real_data(db):
            return None

        def work(db: Session, agent) -> tuple[int, str]:
            rules = agent_rules(agent)
            account = get_ml_account(db)
            if account is None or not account.access_token_enc:
                return 0, "Aguardando o Mercado Livre ser conectado"
            room = int(rules["sugestoes_por_semana"]) - weekly_used(db)
            weak = candidates(db, rules["nota_minima_qualidade"])
            if room <= 0 or not weak:
                return 0, f"{len(weak)} anúncio(s) fraco(s) · limite da semana {'atingido' if room <= 0 else 'livre'}"
            ai = AIClient(db, transport)
            store_name = get_setting(db, "store_name") or account.account_name or "nossa loja"
            factory = ml_client_factory or (lambda: MLClient(MLTokenProvider(session_factory)))
            done = 0
            with factory() as ml:
                for quality in weak[:min(MAX_PER_RUN, room)]:
                    try:
                        if process_listing(db, quality, ml, ai, store_name):
                            done += 1
                        db.commit()
                    except (AINotConfigured, BudgetExceeded) as exc:
                        db.commit()
                        return done, str(exc)
                    except (AIError, IntegrationError, NotConnected) as exc:
                        db.rollback()
                        logger.warning("Anúncio %s ficou para a próxima rodada: %s", quality.item_id, exc)
            return done, f"{done} sugestão(ões) nova(s) · {len(weak) - done} anúncio(s) fraco(s) na fila"

        return run_agent(db, SLUG, work)
