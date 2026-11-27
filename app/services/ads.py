"""Mercado Ads: resumo do investimento e do retorno (dados lidos em app/sync/ml_ads.py).

- ROAS = receita gerada pelos anúncios ÷ investimento (quanto volta para cada R$ 1);
- ACOS = investimento ÷ receita dos anúncios × 100;
- TACOS = investimento ÷ faturamento TOTAL no Mercado Livre × 100 (peso real da
  publicidade no negócio, contando as vendas sem anúncio).
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timeutil import local_day_bounds_utc, local_today
from app.integrations.mercadolivre.auth import get_ml_account
from app.models import AdCampaign, AdDaily, AdGroup, Order
from app.models.enums import Channel, KanbanStatus
from app.sync.ml_ads import NO_SALES_MIN_COST_CENTS

PERIODS = {"7": "Últimos 7 dias", "30": "Últimos 30 dias", "90": "Últimos 90 dias"}
STATUS_TEXT = {
    "sem_ads": "A conta do Mercado Livre não tem o Product Ads (anúncios patrocinados) ativo. "
               "Para ativar: Mercado Livre → Meu perfil → Publicidade.",
    "sem_permissao": "O aplicativo do painel não tem a permissão Publicidade. No DevCenter do Mercado Livre, "
                     "marque Publicidade (leitura) no aplicativo e depois clique em “Conectar de novo” em "
                     "Contas conectadas.",
}


def ads_cost_between(db: Session, start: date, end: date) -> int:
    """Investimento em Mercado Ads entre duas datas (inclusive), em centavos."""
    return int(db.scalar(select(func.coalesce(func.sum(AdDaily.cost_cents), 0))
                         .where(AdDaily.day >= start, AdDaily.day <= end)) or 0)


@dataclass
class DayRow:
    day: date
    cost: int = 0
    revenue: int = 0

    @property
    def roas(self) -> float | None:
        return self.revenue / self.cost if self.cost else None


@dataclass
class AdsSummary:
    status: str | None  # ok | sem_ads | sem_permissao | None (nunca leu)
    message: str | None
    days: int
    cost: int = 0
    revenue: int = 0
    clicks: int = 0
    sales: int = 0
    ml_revenue: int = 0  # faturamento total no Mercado Livre no período
    daily: list[DayRow] = field(default_factory=list)
    campaigns: list[AdCampaign] = field(default_factory=list)
    groups: list[AdGroup] = field(default_factory=list)
    wasting: int = 0

    @property
    def roas(self) -> float | None:
        return self.revenue / self.cost if self.cost else None

    @property
    def acos(self) -> float | None:
        return self.cost / self.revenue * 100 if self.revenue else None

    @property
    def tacos(self) -> float | None:
        return self.cost / self.ml_revenue * 100 if self.ml_revenue else None


def ads_summary(db: Session, days: int = 30) -> AdsSummary:
    account = get_ml_account(db)
    status = (account.extra or {}).get("ads_status") if account else None
    result = AdsSummary(status=status, message=STATUS_TEXT.get(status or ""), days=days)
    today = local_today()
    start = today - timedelta(days=days - 1)
    by_day = {start + timedelta(days=i): DayRow(start + timedelta(days=i)) for i in range(days)}
    for row in db.scalars(select(AdDaily).where(AdDaily.day >= start, AdDaily.day <= today)):
        d = by_day.get(row.day)
        if d:
            d.cost += row.cost_cents
            d.revenue += row.revenue_cents
        result.cost += row.cost_cents
        result.revenue += row.revenue_cents
        result.clicks += row.clicks
        result.sales += row.sales
    result.daily = list(by_day.values())
    utc_start, utc_end = local_day_bounds_utc(start)[0], local_day_bounds_utc(today)[1]
    result.ml_revenue = int(db.scalar(select(func.coalesce(func.sum(Order.items_total_cents), 0)).where(
        Order.channel == Channel.ML, Order.ordered_at >= utc_start, Order.ordered_at < utc_end,
        Order.kanban_status != KanbanStatus.CANCELLED, Order.is_demo.is_(False))) or 0)
    result.campaigns = list(db.scalars(select(AdCampaign).order_by(AdCampaign.cost_cents.desc())))
    result.groups = list(db.scalars(select(AdGroup).order_by(AdGroup.cost_cents.desc()).limit(60)))
    result.wasting = sum(1 for g in result.groups if g.cost_cents >= NO_SALES_MIN_COST_CENTS and g.sales == 0)
    return result


def group_roas(group: AdGroup) -> float | None:
    return group.revenue_cents / group.cost_cents if group.cost_cents else None
