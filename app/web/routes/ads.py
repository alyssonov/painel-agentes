"""Tela Publicidade (Mercado Ads): investimento, retorno, campanhas e anúncios. Só leitura."""

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.timeutil import to_local
from app.integrations.mercadolivre.auth import get_ml_account
from app.models.user import User
from app.services.ads import PERIODS, ads_summary, group_roas
from app.services.charts import DayPoint, bar_chart
from app.sync.ml_ads import NO_SALES_MIN_COST_CENTS
from app.web.auth import current_user
from app.web.templating import render

router = APIRouter(prefix="/publicidade")

STRATEGIES = {"PROFITABILITY": "Rentabilidade", "INCREASE": "Crescimento", "VISIBILITY": "Visibilidade"}


@router.get("")
def ads_page(request: Request, periodo: str = "30", db: Session = Depends(get_db),
             user: User = Depends(current_user)):
    periodo = periodo if periodo in PERIODS else "30"
    summary = ads_summary(db, int(periodo))
    account = get_ml_account(db)
    points = [DayPoint(d.day, revenue=d.cost) for d in summary.daily]
    last = (account.extra or {}).get("cursor_ads") if account else None
    return render(request, "ads.html", {
        "s": summary, "periodo": periodo, "periods": PERIODS,
        "chart": bar_chart(points, "revenue", show_orders=False) if summary.cost else None,
        "strategies": STRATEGIES, "group_roas": group_roas, "min_cost": NO_SALES_MIN_COST_CENTS,
        "ml_connected": bool(account and account.access_token_enc),
        "last_read": to_local(datetime.fromisoformat(last)).strftime("%d/%m às %H:%M")
        if last else None,
    }, db=db)
