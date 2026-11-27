"""DRE mensal (demonstrativo do resultado) — vendas do Olist + despesas lançadas no painel.

Cada venda usa a MESMA conta da tela Lucro real (`order_margin`): o que o
marketplace descontou vira comissão + frete/tarifas (no ML, a partir do valor
realmente recebido no Mercado Pago), custo vem do Olist e imposto é a alíquota
da loja. Depois entram as despesas fixas do mês. Só leitura de vendas.

    Receita bruta de vendas
    (−) comissões dos marketplaces
    (−) frete e tarifas
    (−) impostos sobre vendas
    = receita líquida
    (−) custo dos produtos vendidos
    (−) embalagem e outros custos por venda
    = lucro bruto (margem de contribuição)
    (−) despesas (por categoria) — inclui o Mercado Ads lido automaticamente
    = lucro líquido do mês
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import local_day_bounds_utc, local_today
from app.models.expense import CATEGORIES, Expense
from app.services.dashboard import _orders_between, money_confirmed, order_margin
from app.services.store_settings import get_setting
from app.services.store_settings import other_costs_cents

# Linha automática do DRE: investimento no Mercado Ads (Product Ads), lido do Mercado Livre.
ADS_CATEGORY = "mercado_ads"
ADS_LABEL = "Mercado Ads sem venda no dia (automático)"

MONTHS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
          "novembro", "dezembro"]


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def month_label(d: date) -> str:
    return f"{MONTHS[d.month - 1]}/{d.year}"


def parse_month(text: str | None) -> date | None:
    """"2026-11" → 1º/11/2026."""
    try:
        y, m = (int(x) for x in (text or "").split("-")[:2])
        return date(y, m, 1)
    except (TypeError, ValueError):
        return None


@dataclass
class MonthDRE:
    month: date
    revenue: int = 0
    fees: int = 0
    shipping: int = 0
    taxes: int = 0
    cost: int = 0
    other: int = 0  # embalagem, etiqueta etc. (valor médio por venda × vendas)
    ads: int = 0  # publicidade dividida nas vendas (o resto do gasto fica nas despesas)
    orders: int = 0
    unconfirmed: int = 0  # vendas com taxa/frete ainda não conferidos
    missing_cost: int = 0  # vendas com produto sem custo no Olist
    expenses: dict[str, int] = field(default_factory=dict)  # categoria → total
    partial: bool = False  # mês atual (ainda não acabou)

    @property
    def label(self) -> str:
        return month_label(self.month)

    @property
    def net_revenue(self) -> int:
        return self.revenue - self.fees - self.shipping - self.taxes

    @property
    def gross_profit(self) -> int:
        return self.net_revenue - self.cost - self.other - self.ads

    @property
    def total_expenses(self) -> int:
        return sum(self.expenses.values())

    @property
    def net_profit(self) -> int:
        return self.gross_profit - self.total_expenses

    def pct(self, value: int) -> float | None:
        return value / self.revenue * 100 if self.revenue else None

    @property
    def break_even(self) -> int | None:
        """Faturamento que paga as despesas fixas, com a margem de contribuição deste mês."""
        if not self.revenue or self.gross_profit <= 0:
            return None
        return round(self.total_expenses / (self.gross_profit / self.revenue))


def expenses_for(db: Session, month: date) -> list[Expense]:
    return [e for e in db.scalars(select(Expense).where(Expense.start_month <= month).order_by(Expense.name))
            if e.applies_to(month)]


def month_dre(db: Session, month: date) -> MonthDRE:
    month = month_start(month)
    nxt = add_months(month, 1)
    start = local_day_bounds_utc(month)[0]
    end = local_day_bounds_utc(nxt)[0]
    tax_rate = float(get_setting(db, "tax_rate_pct") or 0)
    other = other_costs_cents(db)
    result = MonthDRE(month=month, partial=month <= local_today() < nxt)
    for order in _orders_between(db, start, end):
        m = order_margin(order, tax_rate, other)
        result.revenue += m.revenue_cents
        result.fees += m.fee_cents
        result.shipping += m.shipping_cents
        result.taxes += m.tax_cents
        result.cost += m.cost_cents
        result.other += m.other_cents
        result.ads += m.ads_cents
        result.orders += 1
        result.unconfirmed += not money_confirmed(order)
        result.missing_cost += any(not item.product or not item.product.cost_cents for item in order.items)
    for expense in expenses_for(db, month):
        result.expenses[expense.category] = result.expenses.get(expense.category, 0) + expense.amount_cents
    # Mercado Ads lido automaticamente do Mercado Livre (até hoje, no mês atual).
    from app.services.ads import ads_cost_between  # import local: evita ciclo
    ads = ads_cost_between(db, month, min(add_months(month, 1) - timedelta(days=1), local_today()))
    # A parte que já foi descontada venda a venda não entra de novo: fica só o gasto
    # de dias sem venda no Mercado Livre (ou ainda não dividido).
    if ads - result.ads > 0:
        result.expenses[ADS_CATEGORY] = ads - result.ads
    return result


def dre_months(db: Session, last: date, count: int = 3) -> list[MonthDRE]:
    """O mês escolhido e os anteriores (mais antigo primeiro)."""
    return [month_dre(db, add_months(month_start(last), -i)) for i in range(count - 1, -1, -1)]


def category_label(key: str) -> str:
    if key == ADS_CATEGORY:
        return ADS_LABEL
    return CATEGORIES.get(key, key)
