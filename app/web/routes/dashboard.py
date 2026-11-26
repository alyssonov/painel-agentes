"""Visão geral do painel."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.user import User
from app.services import dashboard
from app.web.auth import current_user
from app.web.templating import render

router = APIRouter()

CHANNEL_FILTERS = [("", "Todos"), ("ml", "Mercado Livre"), ("shopee", "Shopee")]


@router.get("/")
def overview(request: Request, db: Session = Depends(get_db), _user: User = Depends(current_user)):
    from app.agents.config import has_real_data
    from app.services.charts import bar_chart, daily_points
    from app.services.month import month_summary
    from app.services.store_settings import get_setting

    points = daily_points(db, 30, float(get_setting(db, "tax_rate_pct") or 0), demo=not has_real_data(db))
    return render(request, "overview.html", {
        "ov": dashboard.overview(db),
        "month": month_summary(db),
        "revenue_chart": bar_chart(points, "revenue", height=220),
        "chart_total": sum(p.revenue for p in points),
        "columns": dashboard.kanban(db),
        "channel": "",
        "channel_filters": CHANNEL_FILTERS,
    }, db=db)


@router.get("/parciais/pedidos")
def orders_partial(request: Request, canal: str = "", db: Session = Depends(get_db),
                   _user: User = Depends(current_user)):
    """Só o quadro de pedidos — usado pelo filtro Todos / Mercado Livre / Shopee."""
    return render(request, "partials/kanban.html", {
        "columns": dashboard.kanban(db, canal or None),
        "channel": canal,
        "channel_filters": CHANNEL_FILTERS,
    })


@router.get("/parciais/agentes")
def agents_partial(request: Request, db: Session = Depends(get_db), _user: User = Depends(current_user)):
    """Lista 'Agentes agora' — atualizada sozinha a cada minuto."""
    return render(request, "partials/agents.html", {"ov": dashboard.overview(db)})
