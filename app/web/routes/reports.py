"""Páginas "Estoque e preços" e "Margem real" (Fase 4)."""

from datetime import timedelta

import csv
import io

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.config import agent_rules, has_real_data
from app.core.db import get_db, utcnow
from app.core.timeutil import local_day_bounds_utc, local_today, to_local
from app.models import Agent, Order, Payment
from app.models.enums import CHANNEL_LABELS, Channel, KanbanStatus
from app.services.dashboard import money_confirmed, order_margin
from app.models.user import User
from app.core.money import format_brl
from app.services.margin_report import margin_report
from app.services.abc import CLASS_INFO, abc_curve
from app.services.charts import bar_chart, daily_points
from app.services.idle import IDLE_LABELS, idle_products
from app.services.receivables import forecast
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


ABC_PERIODS = [("30", "30 dias"), ("90", "90 dias"), ("120", "120 dias")]
ABC_CRITERIA = [("faturamento", "Por faturamento"), ("lucro", "Por lucro real")]


def _abc(db: Session, periodo: str, criterio: str):
    end = utcnow()
    start = end - timedelta(days=int(periodo))
    demo = not has_real_data(db)
    report = margin_report(db, start, end, float(get_setting(db, "tax_rate_pct") or 0), demo=demo)
    rows, summary = abc_curve(report.products, criterio)
    oldest = db.scalar(select(func.min(Order.ordered_at)).where(
        Order.is_demo.is_(demo), Order.kanban_status != KanbanStatus.CANCELLED))
    partial = not demo and (oldest is None or oldest > start + timedelta(days=1))
    return rows, summary, report, oldest, partial


@router.get("/margem/curva-abc")
def abc_page(request: Request, periodo: str = "90", criterio: str = "faturamento", db: Session = Depends(get_db),
             user: User = Depends(current_user)):
    periodo = periodo if periodo in dict(ABC_PERIODS) else "90"
    criterio = criterio if criterio in dict(ABC_CRITERIA) else "faturamento"
    rows, summary, report, oldest, partial = _abc(db, periodo, criterio)
    return render(request, "abc.html", {
        "rows": rows, "summary": summary, "class_info": CLASS_INFO, "periodo": periodo, "criterio": criterio,
        "periods": ABC_PERIODS, "criteria": ABC_CRITERIA, "partial": partial,
        "oldest": to_local(oldest).strftime("%d/%m/%Y") if oldest else None,
        "missing_cost": report.products_missing_cost,
    }, db=db)


@router.get("/margem/curva-abc.csv")
def abc_csv(periodo: str = "90", criterio: str = "faturamento", db: Session = Depends(get_db),
            user: User = Depends(current_user)):
    """Planilha da curva ABC (separador ";" e acentos corretos para o Excel)."""
    periodo = periodo if periodo in dict(ABC_PERIODS) else "90"
    criterio = criterio if criterio in dict(ABC_CRITERIA) else "faturamento"
    rows, *_ = _abc(db, periodo, criterio)
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["Posição", "Classe", "SKU", "Produto", "Unidades", "Faturamento", "Lucro real",
                     "% do total", "% acumulado"])
    for position, row in enumerate(rows, 1):
        p = row.product
        writer.writerow([position, row.cls, p.sku, p.name, p.units, format_brl(p.revenue), format_brl(p.profit),
                         f"{row.share:.1f}".replace(".", ","), f"{row.cumulative:.1f}".replace(".", ",")])
    filename = f"curva-abc-{criterio}-{periodo}dias-{local_today():%Y-%m-%d}.csv"
    return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


WEEKDAYS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


@router.get("/margem/recebimentos")
def receivables_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    result = forecast(db, demo=not has_real_data(db))
    today = local_today()
    days = [{"label": "Hoje" if d.day == today else "Amanhã" if d.day == today + timedelta(days=1)
             else f"{WEEKDAYS[d.day.weekday()]} {d.day:%d/%m}", "d": d} for d in result.days]
    return render(request, "receivables.html", {"f": result, "days": days}, db=db)


CHART_PERIODS = [("30", "30 dias"), ("90", "90 dias")]


@router.get("/margem/graficos")
def charts_page(request: Request, periodo: str = "30", db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    periodo = periodo if periodo in dict(CHART_PERIODS) else "30"
    days = int(periodo)
    demo = not has_real_data(db)
    points = daily_points(db, days, float(get_setting(db, "tax_rate_pct") or 0), demo=demo)
    revenue, profit = sum(p.revenue for p in points), sum(p.profit for p in points)
    best = max(points, key=lambda p: p.revenue) if points else None
    oldest = db.scalar(select(func.min(Order.ordered_at)).where(
        Order.is_demo.is_(demo), Order.kanban_status != KanbanStatus.CANCELLED))
    partial = not demo and (oldest is None or to_local(oldest).date() > points[0].day)
    return render(request, "charts.html", {
        "periodo": periodo, "periods": CHART_PERIODS, "points": points,
        "revenue_chart": bar_chart(points, "revenue"), "profit_chart": bar_chart(points, "profit"),
        "revenue": revenue, "profit": profit, "margin": profit / revenue * 100 if revenue else None,
        "avg_day": revenue // days if days else 0, "best": best,
        "loss_days": sum(1 for p in points if p.profit < 0),
        "partial": partial, "oldest": to_local(oldest).strftime("%d/%m/%Y") if oldest else None,
    }, db=db)


def _find_order(db: Session, number: str) -> Order | None:
    """Acha a venda pelo número do Mercado Livre (venda ou pacote), da Shopee ou do pedido no Olist."""
    number = "".join(number.split()).lstrip("#")
    if not number:
        return None
    demo = not has_real_data(db)
    base = select(Order).where(Order.is_demo.is_(demo))
    return (db.scalar(base.where(Order.external_id == number))
            or db.scalar(base.where(Order.olist_number == number))
            or db.scalar(base.where(Order.olist_id == number)))


@router.get("/margem/venda")
def sale_lookup(request: Request, numero: str = "", db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    """Conferir uma venda: busca pelo número e mostra a conta do lucro linha por linha."""
    order = _find_order(db, numero) if numero else None
    if order is not None:
        return RedirectResponse(f"/margem/venda/{order.id}", status_code=303)
    demo = not has_real_data(db)
    recent = list(db.scalars(select(Order).where(Order.is_demo.is_(demo), Order.kanban_status != KanbanStatus.CANCELLED)
                             .order_by(Order.ordered_at.desc()).limit(30)))
    return render(request, "sale.html", {"order": None, "numero": numero, "not_found": bool(numero),
                                         "recent": recent}, db=db)


@router.get("/margem/venda/{order_id}")
def sale_detail(request: Request, order_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    order = db.get(Order, order_id)
    if order is None:
        return RedirectResponse("/margem/venda", status_code=303)
    tax = float(get_setting(db, "tax_rate_pct") or 0)
    m = order_margin(order, tax)
    payments = list(db.scalars(select(Payment).where(Payment.channel == order.channel,
                                                     Payment.order_ref == order.external_id)
                               .order_by(Payment.approved_at)))
    items = [{"title": i.title, "sku": i.sku, "qty": i.quantity, "price": i.unit_price_cents,
              "unit_cost": i.product.cost_cents if i.product else 0,
              "missing_cost": not (i.product and i.product.cost_cents > 0)} for i in order.items]
    return render(request, "sale.html", {
        "order": order, "m": m, "tax_rate": tax, "items": items, "payments": payments,
        "confirmed": money_confirmed(order), "channel_label": CHANNEL_LABELS.get(Channel(order.channel), order.channel),
        "when": to_local(order.ordered_at).strftime("%d/%m/%Y às %H:%M"), "numero": "", "recent": [],
    }, db=db)
