"""Dados da Visão geral do painel.

Tudo aqui é leitura do banco local (que espelha Olist e marketplaces).
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.core.timeutil import local_day_bounds_utc, local_today, to_local, utcnow
from app.models import Agent, AgentRun, AiUsage, Alert, IntegrationAccount, Order, OrderItem, Question
from app.models.enums import (
    CHANNEL_LABELS,
    KANBAN_LABELS,
    PROVIDER_LABELS,
    AlertSeverity,
    AlertStatus,
    Channel,
    IntegrationStatus,
    KanbanStatus,
    Provider,
    QuestionStatus,
)
from app.services.margin import MarginResult, calculate_margin
from app.services.store_settings import get_setting

_SEVERITY_ORDER = {AlertSeverity.CRITICAL: 0, AlertSeverity.WARNING: 1, AlertSeverity.INFO: 2}


@dataclass
class IntegrationView:
    provider: str
    label: str
    status: str
    detail: str


@dataclass
class OrderCard:
    id: int
    channel: str
    channel_label: str
    external_id: str
    customer: str
    summary: str
    total_cents: int
    time_label: str
    problem: str | None
    is_loss: bool


@dataclass
class KanbanColumn:
    status: str
    label: str
    orders: list[OrderCard] = field(default_factory=list)


@dataclass
class AgentView:
    slug: str
    name: str
    enabled: bool
    mode: str
    status: str  # ok | erro | pausado | sem_execucao
    metric: str
    last_run_label: str | None


@dataclass
class Overview:
    revenue_today_cents: int
    revenue_yesterday_cents: int
    orders_today: int
    avg_margin_pct: float | None
    loss_orders: int
    questions_answered: int
    questions_today: int
    questions_waiting: int
    reputation_ml: str | None
    reputation_shopee: str | None
    alerts: list[Alert]
    agents: list[AgentView]
    ai_cost_today_cents: int
    ai_budget_cents: int


def order_margin(order: Order, tax_rate_pct: float) -> MarginResult:
    """Margem real de um pedido (custo dos itens vem do cadastro do Olist)."""
    cost = sum(item.quantity * (item.product.cost_cents if item.product else 0) for item in order.items)
    return calculate_margin(
        revenue_cents=order.items_total_cents,
        fee_cents=order.marketplace_fee_cents,
        shipping_cents=order.shipping_cost_cents,
        cost_cents=cost,
        tax_rate_pct=tax_rate_pct,
    )


def _orders_between(db: Session, start: datetime, end: datetime, channel: str | None = None) -> list[Order]:
    stmt = (
        select(Order)
        .where(Order.ordered_at >= start, Order.ordered_at < end)
        .options(selectinload(Order.items).selectinload(OrderItem.product))
        .order_by(Order.ordered_at.desc())
    )
    if channel:
        stmt = stmt.where(Order.channel == channel)
    return list(db.scalars(stmt))


def integrations(db: Session) -> list[IntegrationView]:
    """Status das contas conectadas, na ordem do menu lateral."""
    accounts = {a.provider: a for a in db.scalars(select(IntegrationAccount))}
    views = []
    for provider in (Provider.ML, Provider.SHOPEE, Provider.OLIST):
        account = accounts.get(provider)
        if account is None:
            views.append(IntegrationView(provider, PROVIDER_LABELS[provider],
                                         IntegrationStatus.DISCONNECTED, "Não conectado"))
            continue
        detail = {
            IntegrationStatus.OK: "Conectado",
            IntegrationStatus.ERROR: "Com erro",
            IntegrationStatus.DISCONNECTED: "Desconectado",
        }.get(account.status, account.status)
        views.append(IntegrationView(provider, PROVIDER_LABELS[provider], account.status, detail))
    return views


def kanban(db: Session, channel: str | None = None) -> list[KanbanColumn]:
    """Quadro 'Pedidos de hoje', com filtro opcional por marketplace."""
    if channel not in (None, Channel.ML, Channel.SHOPEE):
        channel = None
    start, end = local_day_bounds_utc(local_today())
    tax_rate = float(get_setting(db, "tax_rate_pct") or 0)
    columns = {status: KanbanColumn(status, KANBAN_LABELS[status]) for status in KanbanStatus}
    for order in _orders_between(db, start, end, channel):
        first = order.items[0].title if order.items else "Pedido"
        extra = len(order.items) - 1
        summary = first + (f" + {extra} item" + ("s" if extra > 1 else "") if extra > 0 else "")
        card = OrderCard(
            id=order.id,
            channel=order.channel,
            channel_label=CHANNEL_LABELS.get(Channel(order.channel), order.channel),
            external_id=order.external_id,
            customer=order.customer_short_name,
            summary=summary,
            total_cents=order.items_total_cents,
            time_label=to_local(order.ordered_at).strftime("%H:%M"),
            problem=order.problem_note,
            is_loss=order_margin(order, tax_rate).is_loss,
        )
        columns.get(KanbanStatus(order.kanban_status), columns[KanbanStatus.PROBLEM]).orders.append(card)
    return list(columns.values())


def _agents(db: Session) -> list[AgentView]:
    agents = list(db.scalars(select(Agent).order_by(Agent.position)))
    # Última execução de cada agente.
    latest_ids = select(func.max(AgentRun.id)).group_by(AgentRun.agent_id)
    runs = {r.agent_id: r for r in db.scalars(select(AgentRun).where(AgentRun.id.in_(latest_ids)))}
    views = []
    for agent in agents:
        run = runs.get(agent.id)
        if not agent.enabled:
            status = "pausado"
        elif run is None:
            status = "sem_execucao"
        else:
            status = run.status
        views.append(AgentView(
            slug=agent.slug,
            name=agent.name,
            enabled=agent.enabled,
            mode=agent.mode,
            status=status,
            metric=(run.summary if run and run.summary else "Ainda não rodou"),
            last_run_label=to_local(run.finished_at or run.started_at).strftime("%H:%M") if run else None,
        ))
    return views


def overview(db: Session) -> Overview:
    """Números e listas da Visão geral."""
    today = local_today()
    start, end = local_day_bounds_utc(today)
    now = utcnow()
    tax_rate = float(get_setting(db, "tax_rate_pct") or 0)

    orders_today = _orders_between(db, start, end)
    margins = [order_margin(o, tax_rate) for o in orders_today]
    revenue = sum(m.revenue_cents for m in margins)
    profit = sum(m.profit_cents for m in margins)

    # Faturamento de ontem até o mesmo horário, para comparar de forma justa.
    y_start = start - (end - start)
    y_now = min(now - (end - start), start)
    revenue_yesterday = db.scalar(
        select(func.coalesce(func.sum(Order.items_total_cents), 0)).where(
            Order.ordered_at >= y_start, Order.ordered_at < y_now
        )
    )

    q_today = db.scalar(select(func.count()).select_from(Question).where(
        Question.asked_at >= start, Question.asked_at < end))
    q_answered = db.scalar(select(func.count()).select_from(Question).where(
        Question.asked_at >= start, Question.asked_at < end, Question.status == QuestionStatus.ANSWERED))
    q_waiting = db.scalar(select(func.count()).select_from(Question).where(
        Question.status.in_([QuestionStatus.NEW, QuestionStatus.PROPOSED, QuestionStatus.HUMAN])))

    accounts = {a.provider: a for a in db.scalars(select(IntegrationAccount))}
    ml, shopee = accounts.get(Provider.ML), accounts.get(Provider.SHOPEE)
    rep_ml = (ml.extra or {}).get("reputation_label") if ml else None
    rating = (shopee.extra or {}).get("rating") if shopee else None
    rep_shopee = f"{rating:.1f}".replace(".", ",") + " ★" if rating else None

    alerts = sorted(
        db.scalars(select(Alert).where(Alert.status == AlertStatus.OPEN)),
        key=lambda a: (_SEVERITY_ORDER.get(AlertSeverity(a.severity), 9), -a.id),
    )

    ai_millicents = db.scalar(select(func.coalesce(func.sum(AiUsage.cost_brl_millicents), 0)).where(
        AiUsage.at >= start, AiUsage.at < end))

    return Overview(
        revenue_today_cents=revenue,
        revenue_yesterday_cents=int(revenue_yesterday or 0),
        orders_today=len(orders_today),
        avg_margin_pct=(profit / revenue * 100) if revenue else None,
        loss_orders=sum(1 for m in margins if m.is_loss),
        questions_answered=q_answered or 0,
        questions_today=q_today or 0,
        questions_waiting=q_waiting or 0,
        reputation_ml=rep_ml,
        reputation_shopee=rep_shopee,
        alerts=alerts,
        agents=_agents(db),
        ai_cost_today_cents=round((ai_millicents or 0) / 1000),
        ai_budget_cents=round(get_settings().ai_daily_budget_brl * 100),
    )
