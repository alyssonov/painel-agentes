"""Páginas "Estoque e preços" e "Margem real" (Fase 4)."""

from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.config import agent_rules, has_real_data
from app.core.db import get_db, utcnow
from app.core.timeutil import local_day_bounds_utc, local_today, to_local
from app.models import Agent
from app.models.user import User
from app.services.margin_report import margin_report
from app.services.stock import stock_rows
from app.services.store_settings import get_setting
from app.web.auth import current_user
from app.web.templating import render

router = APIRouter()

PERIODS = [("hoje", "Hoje"), ("7", "7 dias"), ("30", "30 dias")]
STATUS_LABELS = {"esgotado": ("Esgotado", "erro"), "baixo": ("Acabando", "atencao"), "ok": ("Em dia", "ok"),
                 "parado": ("Sem vendas", "pausado"), "sem_info": ("Sem leitura", "info")}


def _rules(db: Session, slug: str) -> dict[str, float]:
    agent = db.scalar(select(Agent).where(Agent.slug == slug))
    return agent_rules(agent) if agent else {}


@router.get("/estoque-precos")
def stock_page(request: Request, filtro: str = "atencao", db: Session = Depends(get_db),
               user: User = Depends(current_user)):
    rules = _rules(db, "estoque")
    rows = stock_rows(db, window_days=int(rules.get("janela_vendas_dias", 30)),
                      alert_days=rules.get("dias_alerta", 7), tolerance=int(rules.get("tolerancia_divergencia", 0)),
                      demo=not has_real_data(db))
    counts = {
        "esgotado": sum(r.status == "esgotado" for r in rows),
        "baixo": sum(r.status == "baixo" for r in rows),
        "divergencia": sum(r.diverges for r in rows),
        "ok": sum(r.status == "ok" for r in rows),
    }
    shown = rows if filtro == "todos" else [r for r in rows if r.status in ("esgotado", "baixo") or r.diverges]
    return render(request, "stock.html", {
        "rows": shown, "total": len(rows), "counts": counts, "filtro": filtro, "status_labels": STATUS_LABELS,
        "window": int(rules.get("janela_vendas_dias", 30)), "alert_days": int(rules.get("dias_alerta", 7)),
    }, db=db)


@router.get("/margem")
def margin_page(request: Request, periodo: str = "7", db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    if periodo not in dict(PERIODS):
        periodo = "7"
    if periodo == "hoje":
        start, end = local_day_bounds_utc(local_today())
    else:
        end = utcnow()
        start = end - timedelta(days=int(periodo))
    tax = float(get_setting(db, "tax_rate_pct") or 0)
    report = margin_report(db, start, end, tax, demo=not has_real_data(db))
    min_margin = _rules(db, "margem").get("margem_minima_pct", 5)
    return render(request, "margin.html", {
        "r": report, "periodo": periodo, "periods": PERIODS, "tax_rate": tax, "min_margin": min_margin,
        "loss_orders": [{"o": o, "when": to_local(o.ordered_at).strftime("%d/%m %H:%M")} for o in report.loss_orders[:30]],
    }, db=db)
