"""Agente 6 — Avaliações e devoluções.

Só LÊ e avisa (nenhuma escrita em marketplace):
1. Por produto (90 dias): nota média, avaliações negativas, reclamações/devoluções
   por motivo e taxa de problema (reclamações ÷ unidades vendidas).
2. Alertas na faixa "O que pede atenção": taxa de problema alta ou nota baixa.
3. Uma vez por semana por produto, e só para quem acumula problemas, a IA
   (modelo da tarefa "analise_avaliacoes") resume o que se repete e sugere o que
   corrigir. Poucos produtos por rodada, contexto curto e cache: custo baixo.
"""

import hashlib
import logging
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.base import run_agent
from app.agents.config import agent_rules, has_real_data
from app.ai.client import AIClient, AIError, AINotConfigured, AIRefused, BudgetExceeded, Transport
from app.core.alerts import AlertSpec, sync_alerts
from app.core.db import utcnow
from app.models import ProductInsight
from app.models.enums import AlertSeverity
from app.services.quality import ProductQuality, product_quality

logger = logging.getLogger("agentes.avaliacoes")

SLUG = "avaliacoes"
MAX_AI_PER_RUN = 5
MAX_REVIEWS_IN_PROMPT = 12
INSIGHT_EVERY = timedelta(days=7)
PROMPT = (Path(__file__).resolve().parent.parent / "ai" / "prompts" / "analise_avaliacoes.md").read_text("utf-8")

SCHEMA = {
    "type": "object",
    "properties": {
        "resumo": {"type": "string"},
        "problemas": {"type": "array", "items": {
            "type": "object",
            "properties": {"problema": {"type": "string"}, "evidencia": {"type": "string"}},
            "required": ["problema", "evidencia"], "additionalProperties": False}},
        "sugestoes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["resumo", "problemas", "sugestoes"],
    "additionalProperties": False,
}


def needs_insight(q: ProductQuality) -> bool:
    return len(q.low_reviews) >= 2 or q.product_problems >= 2


def insight_input(q: ProductQuality) -> str:
    """Contexto curto: só nome do produto, motivos e textos das avaliações (sem dados de compradores)."""
    lines = [f"PRODUTO: {q.product.name}", f"Vendidos em 90 dias: {q.units}"]
    if q.rating_average is not None:
        lines.append(f"Nota média: {q.rating_average:.1f} ({q.rating_total} avaliações)")
    if q.reasons:
        lines.append("RECLAMAÇÕES/DEVOLUÇÕES POR MOTIVO:")
        lines += [f"- {label}: {count}" for label, count in sorted(q.reasons.items(), key=lambda x: -x[1])]
    if q.low_reviews:
        lines.append("AVALIAÇÕES NEGATIVAS (estrelas: texto):")
        for review in q.low_reviews[:MAX_REVIEWS_IN_PROMPT]:
            text = " ".join(f"{review.title or ''}. {review.content or ''}".split())[:300]
            variation = f" [{review.variation}]" if review.variation else ""
            lines.append(f"- {review.rate}★{variation}: {text}")
    return "\n".join(lines)


def build_alerts(rows: list[ProductQuality], max_rate: float, min_rating: float) -> list[AlertSpec]:
    specs = []
    for q in rows:
        rate = q.claim_rate
        if q.claims >= 2 and rate is not None and rate >= max_rate:
            specs.append(AlertSpec(f"taxa:{q.product.id}", AlertSeverity.WARNING,
                                   f"{q.product.name}: {q.claims} reclamação(ões) em {q.units} vendidos ({rate:.0f}%)",
                                   (q.insight.summary if q.insight else "Veja os motivos em Pós-venda → Avaliações "
                                    "e devoluções."), "avaliacoes"))
        elif q.rating_average is not None and q.rating_total >= 5 and q.rating_average < min_rating:
            specs.append(AlertSpec(f"nota:{q.product.id}", AlertSeverity.WARNING,
                                   f"{q.product.name}: nota {q.rating_average:.1f} nas avaliações",
                                   q.insight.summary if q.insight else None, "avaliacoes"))
    return specs[:10]


def run_reviews_agent(session_factory: sessionmaker[Session], *, transport: Transport | None = None):
    with session_factory() as db:
        if not has_real_data(db):
            return None

        def work(db: Session, agent) -> tuple[int, str]:
            rules = agent_rules(agent)
            rows = product_quality(db)
            done, note = 0, ""
            ai = None
            for q in rows:
                if done >= MAX_AI_PER_RUN or not needs_insight(q):
                    continue
                text = insight_input(q)
                digest = hashlib.sha256(text.encode()).hexdigest()
                current = q.insight
                if current and (current.inputs_hash == digest or utcnow() - current.generated_at < INSIGHT_EVERY):
                    continue
                ai = ai or AIClient(db, transport)
                try:
                    result = ai.complete_json(task="analise_avaliacoes", agent_slug=SLUG, system=PROMPT, user=text,
                                              schema=SCHEMA, cache_key_parts=[digest], max_tokens=1500)
                except (AINotConfigured, BudgetExceeded) as exc:
                    note = str(exc)
                    break
                except (AIRefused, AIError) as exc:
                    logger.warning("Análise do produto %s ficou para depois: %s", q.product.id, exc)
                    continue
                if current is None:
                    current = ProductInsight(product_id=q.product.id, summary="", inputs_hash=digest,
                                             generated_at=utcnow())
                    db.add(current)
                current.summary = (result.get("resumo") or "")[:500]
                current.problems = (result.get("problemas") or [])[:3]
                current.suggestions = [str(s)[:300] for s in (result.get("sugestoes") or [])[:3]]
                current.inputs_hash, current.generated_at = digest, utcnow()
                q.insight = current
                done += 1
                db.commit()
            sync_alerts(db, SLUG, build_alerts(rows, rules["limite_reclamacoes_pct"], rules["nota_minima"]))
            db.commit()
            with_problems = sum(1 for q in rows if q.claims or q.low_reviews)
            summary = f"{with_problems} produto(s) com reclamações ou avaliações negativas em 90 dias"
            if done:
                summary += f" · {done} análise(s) nova(s)"
            return done, note or summary

        return run_agent(db, SLUG, work)

