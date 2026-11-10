"""Páginas "Estoque e preços" e "Margem real" (Fase 4)."""

from datetime import timedelta

import csv
import io

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.config import agent_rules, has_real_data
from app.core.db import get_db, utcnow
from app.core.timeutil import local_day_bounds_utc, local_today, to_local
from app.models import Agent
from app.models.user import User
from app.core.money import format_brl
from app.services.margin_report import margin_report
from app.services.idle import IDLE_LABELS, idle_products
from app.services.purchase import URGENCY_LABELS, purchase_suggestions
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
    lead, coverage = _purchase_rules(rules)
    purchase = purchase_suggestions(db, rows, lead_days=lead, coverage_days=coverage)
    idle_days, excess_days = int(rules.get("dias_sem_venda", 60)), int(rules.get("excesso_dias", 180))
    idle = idle_products(db, rows, idle_days=idle_days, excess_days=excess_days, demo=not has_real_data(db))
    return render(request, "stock.html", {
        "idle": idle, "idle_labels": IDLE_LABELS, "idle_history_note": has_real_data(db) and not idle.history_complete, "idle_days": idle_days, "excess_days": excess_days,
        "idle_since": to_local(idle.history_since).strftime("%d/%m/%Y") if idle.history_since else None,
        "rows": shown, "total": len(rows), "counts": counts, "filtro": filtro, "status_labels": STATUS_LABELS,
        "window": int(rules.get("janela_vendas_dias", 30)), "alert_days": int(rules.get("dias_alerta", 7)),
        "purchase": purchase, "purchase_total": sum(i.total_cost_cents for i in purchase),
        "missing_cost": sum(1 for i in purchase if not i.unit_cost_cents), "urgency_labels": URGENCY_LABELS,
        "lead": int(lead), "coverage": int(coverage),
    }, db=db)


def _purchase_rules(rules: dict[str, float]) -> tuple[float, float]:
    return rules.get("prazo_fornecedor_dias", 10), rules.get("cobertura_dias", 30)


@router.get("/estoque-precos/sugestao-de-compra.csv")
def purchase_csv(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Planilha da sugestão de compra (abre no Excel: separador ";" e acentos corretos)."""
    rules = _rules(db, "estoque")
    rows = stock_rows(db, window_days=int(rules.get("janela_vendas_dias", 30)), demo=not has_real_data(db))
    lead, coverage = _purchase_rules(rules)
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["SKU", "Produto", "Disponível no Olist", "Vendas por dia", "Dura (dias)",
                     "Comprar (unidades)", "Custo unitário", "Custo total", "Urgência"])
    for item in purchase_suggestions(db, rows, lead_days=lead, coverage_days=coverage):
        writer.writerow([item.sku, item.name, item.available, f"{item.avg_daily:.1f}".replace(".", ","),
                         f"{item.days_left:.0f}", item.quantity,
                         format_brl(item.unit_cost_cents) if item.unit_cost_cents else "sem custo no Olist",
                         format_brl(item.total_cost_cents) if item.unit_cost_cents else "",
                         URGENCY_LABELS[item.urgency][0]])
    filename = f"sugestao-de-compra-{local_today():%Y-%m-%d}.csv"
    return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


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
