"""Seção Agentes: ligar/pausar, modo manual/automático e regras de cada agente."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.config import RULES, agent_rules
from app.agents.registry import AGENTS, agent_phase
from app.ai.client import spent_today_brl
from app.config import get_settings
from app.core import audit
from app.core.db import get_db
from app.core.timeutil import to_local
from app.integrations.mercadolivre.auth import get_ml_account
from app.models import Agent, AgentRun
from app.models.enums import AgentMode
from app.models.user import User
from app.services.store_settings import get_setting, set_setting
from app.web.auth import current_user, verify_csrf
from app.web.templating import flash, render

router = APIRouter(prefix="/agentes")

# Agentes que já têm lógica pronta (os outros chegam nas próximas fases).
IMPLEMENTED = {"perguntas", "estoque", "nfe", "margem", "relatorio", "despacho", "reputacao", "posvenda", "precos", "avaliacoes", "anuncios"}
# Frequência de cada agente (minutos), para mostrar no painel.
INTERVALS = {"estoque": 15, "nfe": 15, "margem": 60, "relatorio": 10, "despacho": 15, "reputacao": 60, "posvenda": 5, "precos": 60, "avaliacoes": 60, "anuncios": 60}
RULE_FIELDS = {
    "store_name": ("Nome da loja (como o agente se refere à loja)", 80),
    "answer_tone": ("Tom das respostas", 80),
    "shipping_days": ("Prazo de envio", 200),
    "warranty": ("Garantia", 200),
}


@router.get("")
def agents_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    settings = get_settings()
    latest_ids = select(func.max(AgentRun.id)).group_by(AgentRun.agent_id)
    runs = {r.agent_id: r for r in db.scalars(select(AgentRun).where(AgentRun.id.in_(latest_ids)))}
    cards = []
    for agent in db.scalars(select(Agent).order_by(Agent.position)):
        run = runs.get(agent.id)
        cards.append({
            "agent": agent,
            "implemented": agent.slug in IMPLEMENTED,
            "phase": agent_phase(agent.slug),
            "metric": run.summary if run and run.summary else "Ainda não rodou",
            "last_run": to_local(run.finished_at or run.started_at).strftime("%d/%m %H:%M") if run else None,
            "run_status": run.status if run else None,
            "rules": [(rule, agent_rules(agent)[rule.key]) for rule in RULES.get(agent.slug, [])],
            "interval": INTERVALS.get(agent.slug, settings.agent_questions_minutes),
        })
    ml = get_ml_account(db)
    return render(request, "agents.html", {
        "cards": cards,
        "rules": {key: (label, get_setting(db, key) or "", size) for key, (label, size) in RULE_FIELDS.items()},
        "ai_configured": bool(settings.anthropic_api_key),
        "ml_connected": bool(ml and ml.access_token_enc),
        "ai_spent_brl": spent_today_brl(db),
        "ai_budget_brl": settings.ai_daily_budget_brl,
        "interval": settings.agent_questions_minutes,
        "total_agents": len(AGENTS),
    }, db=db)


def _get_agent(db: Session, slug: str) -> Agent | None:
    return db.scalar(select(Agent).where(Agent.slug == slug))


@router.post("/{slug}/ativo", dependencies=[Depends(verify_csrf)])
def toggle_enabled(request: Request, slug: str, enabled: str = Form(...), db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    agent = _get_agent(db, slug)
    if agent is None or slug not in IMPLEMENTED:
        flash(request, "Este agente ainda não está disponível.", "erro")
        return RedirectResponse("/agentes", status_code=303)
    agent.enabled = enabled == "1"
    audit.record(db, actor=f"user:{user.id}", event="agente.ativado" if agent.enabled else "agente.pausado",
                 target_type="agente", target_id=slug)
    db.commit()
    flash(request, f"{agent.name}: {'ativo' if agent.enabled else 'pausado'}.")
    return RedirectResponse("/agentes", status_code=303)


@router.post("/{slug}/modo", dependencies=[Depends(verify_csrf)])
def set_mode(request: Request, slug: str, mode: str = Form(...), db: Session = Depends(get_db),
             user: User = Depends(current_user)):
    agent = _get_agent(db, slug)
    if agent is None or slug not in IMPLEMENTED or mode not in (AgentMode.MANUAL, AgentMode.AUTO):
        flash(request, "Opção inválida.", "erro")
        return RedirectResponse("/agentes", status_code=303)
    agent.mode = mode
    audit.record(db, actor=f"user:{user.id}", event="agente.modo_alterado", target_type="agente",
                 target_id=slug, details={"modo": mode})
    db.commit()
    flash(request, f"{agent.name}: modo {'automático' if mode == AgentMode.AUTO else 'manual'}.")
    return RedirectResponse("/agentes", status_code=303)


@router.post("/perguntas/regras", dependencies=[Depends(verify_csrf)])
async def save_rules(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    form = await request.form()
    changed = {}
    for key, (_label, size) in RULE_FIELDS.items():
        value = str(form.get(key, "")).strip()[:size]
        if value and value != get_setting(db, key):
            set_setting(db, key, value)
            changed[key] = value
    if changed:
        audit.record(db, actor=f"user:{user.id}", event="loja.regras_alteradas", details=changed)
    db.commit()
    flash(request, "Regras salvas." if changed else "Nada mudou.")
    return RedirectResponse("/agentes", status_code=303)


@router.post("/{slug}/config", dependencies=[Depends(verify_csrf)])
async def save_config(request: Request, slug: str, db: Session = Depends(get_db),
                      user: User = Depends(current_user)):
    """Salva as regras numéricas de um agente (dentro dos limites de cada regra)."""
    agent = _get_agent(db, slug)
    if agent is None or slug not in RULES:
        flash(request, "Este agente não tem regras para ajustar.", "erro")
        return RedirectResponse("/agentes", status_code=303)
    form = await request.form()
    config = dict(agent.config or {})
    errors = []
    for rule in RULES[slug]:
        raw = str(form.get(rule.key, "")).strip().replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            errors.append(rule.label)
            continue
        if not rule.minimum <= value <= rule.maximum:
            errors.append(rule.label)
            continue
        config[rule.key] = value
    if errors:
        flash(request, "Valor fora do permitido em: " + "; ".join(errors), "erro")
        return RedirectResponse("/agentes", status_code=303)
    agent.config = config
    audit.record(db, actor=f"user:{user.id}", event="agente.regras_alteradas", target_type="agente",
                 target_id=slug, details=config)
    db.commit()
    flash(request, f"{agent.name}: regras salvas.")
    return RedirectResponse("/agentes", status_code=303)
