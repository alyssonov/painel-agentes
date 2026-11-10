"""Dados da Visão geral do painel.

Tudo aqui é leitura do banco local (que espelha Olist e marketplaces).
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.core.timeutil import local_day_bounds_utc, local_today, to_local, utcnow
from app.agents.config import has_real_data
from app.integrations.mercadolivre.mapping import DISPATCHED_SUBSTATUSES, left_the_store
from app.models import Agent, AgentRun, AiUsage, Alert, IntegrationAccount, Order, OrderItem, Question, Shipment
from app.models.enums import (
    CHANNEL_LABELS,
    KANBAN_COLUMNS,
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
    is_real: bool = False  # conta de verdade (não a da simulação), com token


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
    # true quando há vendas de hoje cuja taxa do marketplace ainda não é conhecida
    fees_missing: bool = False
    tax_configured: bool = True
    dispatch: "DispatchDay | None" = None  # envios do dia (só com conta real)


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
    """Pedidos do período, sem os cancelados."""
    stmt = (
        select(Order)
        .where(Order.ordered_at >= start, Order.ordered_at < end,
               Order.kanban_status != KanbanStatus.CANCELLED)
        .options(selectinload(Order.items).selectinload(OrderItem.product))
        .order_by(Order.ordered_at.desc())
    )
    if channel:
        stmt = stmt.where(Order.channel == channel)
    return list(db.scalars(stmt))


def integrations(db: Session) -> list[IntegrationView]:
    """Status das contas conectadas, na ordem do menu lateral."""
    # Conta real tem prioridade sobre a conta fictícia da simulação.
    accounts: dict[str, IntegrationAccount] = {}
    for account in db.scalars(select(IntegrationAccount).order_by(IntegrationAccount.is_demo.desc())):
        accounts[account.provider] = account
    views = []
    for provider in (Provider.ML, Provider.SHOPEE, Provider.OLIST):
        account = accounts.get(provider)
        if account is None or (not account.is_demo and not account.access_token_enc):
            views.append(IntegrationView(provider, PROVIDER_LABELS[provider],
                                         IntegrationStatus.DISCONNECTED, "Não conectado"))
            continue
        detail = {
            IntegrationStatus.OK: "Conectado",
            IntegrationStatus.ERROR: "Com erro",
            IntegrationStatus.DISCONNECTED: "Desconectado",
        }.get(account.status, account.status)
        if account.status == IntegrationStatus.OK and account.last_ok_at and not account.is_demo:
            detail = f"Atualizado {to_local(account.last_ok_at).strftime('%H:%M')}"
        views.append(IntegrationView(provider, PROVIDER_LABELS[provider], account.status, detail,
                                     is_real=not account.is_demo))
    return views


SHIPPED_STATUSES = {"shipped", "delivered"}
WAITING_STATUSES = {"pending", "handling", "ready_to_ship"}


@dataclass
class DispatchDay:
    """Resumo dos envios do dia (para o quadro e para o Alerta de despacho)."""

    to_ship: int = 0  # prazo hoje e ainda não saiu
    shipped: int = 0  # já despachado hoje
    late: int = 0  # prazo vencido e ainda não saiu


def _shipment_cards(db: Session, channel: str | None) -> tuple[list[KanbanColumn], DispatchDay]:
    """Quadro "Envios de hoje": pedidos com PRAZO DE DESPACHO hoje + atrasados.

    O dia de envio vem do marketplace (SLA de cada envio), não da data da venda.
    Envios Full ficam de fora (quem despacha é o Mercado Livre).
    """
    columns = {status: KanbanColumn(status, KANBAN_LABELS[status]) for status in KANBAN_COLUMNS}
    day = DispatchDay()
    if channel not in (None, Channel.ML):
        return list(columns.values()), day  # por enquanto só o Mercado Livre informa o prazo
    now = utcnow()
    start, end = local_day_bounds_utc(local_today())
    tax_rate = float(get_setting(db, "tax_rate_pct") or 0)
    rows = db.scalars(select(Shipment).where(
        Shipment.channel == Channel.ML,
        or_(Shipment.logistic_type.is_(None), Shipment.logistic_type != "fulfillment"),
        or_(
            # ainda não saiu e o prazo é hoje (ou já venceu)
            and_(Shipment.status.in_(WAITING_STATUSES), Shipment.dispatch_deadline < end,
                 or_(Shipment.substatus.is_(None), Shipment.substatus.not_in(DISPATCHED_SUBSTATUSES))),
            # saiu hoje (mesmo que o prazo fosse outro dia)
            and_(Shipment.shipped_at >= start, Shipment.shipped_at < end),
            # saiu (enviado, ou coletado/no centro), sem data de saída conhecida, e o prazo era hoje
            and_(or_(Shipment.status.in_(SHIPPED_STATUSES), Shipment.substatus.in_(DISPATCHED_SUBSTATUSES)),
                 Shipment.shipped_at.is_(None),
                 Shipment.dispatch_deadline >= start, Shipment.dispatch_deadline < end),
        )).order_by(Shipment.dispatch_deadline))
    for row in rows:
        order = db.scalar(select(Order).where(Order.channel == Channel.ML, Order.external_id == row.order_ref)
                          .options(selectinload(Order.items).selectinload(OrderItem.product)))
        deadline = to_local(row.dispatch_deadline) if row.dispatch_deadline else None
        problem = order.problem_note if order else None
        if is_shipped(row, order):
            status, label = KanbanStatus.SHIPPED, "enviado"
            day.shipped += 1
        elif row.dispatch_deadline and row.dispatch_deadline <= now:
            status = KanbanStatus.PROBLEM
            problem = f"Prazo venceu {deadline:%d/%m às %H:%M}" if deadline.date() != local_today() \
                else f"Prazo venceu às {deadline:%H:%M}"
            label = "atrasado"
            day.late += 1
        else:
            packed = row.substatus == "printed" or (order and order.kanban_status == KanbanStatus.PACKED)
            status = KanbanStatus.PROBLEM if problem else (KanbanStatus.PACKED if packed else KanbanStatus.TO_PICK)
            label = f"até {deadline:%H:%M}" if deadline else "hoje"
            day.to_ship += 1
        if order and order.items:
            first, extra = order.items[0].title, len(order.items) - 1
            summary = first + (f" + {extra} item" + ("s" if extra > 1 else "") if extra > 0 else "")
        else:
            summary = "Pedido ainda não importado do Olist"
        columns[status].orders.append(OrderCard(
            id=order.id if order else 0, channel=Channel.ML, channel_label=CHANNEL_LABELS[Channel.ML],
            external_id=row.order_ref, customer=order.customer_short_name if order else "—", summary=summary,
            total_cents=order.items_total_cents if order else 0, time_label=label, problem=problem,
            is_loss=order_margin(order, tax_rate).is_loss if order else False))
    return list(columns.values()), day


def is_shipped(row: Shipment, order: Order | None) -> bool:
    """Saiu da loja? Vale o Mercado Livre (enviado, ou coletado/no centro) OU o Olist ("Enviado"/"Entregue")."""
    return left_the_store(row.status, row.substatus) or bool(order and order.kanban_status == KanbanStatus.SHIPPED)


def dispatch_day(db: Session) -> DispatchDay:
    """Quantos pedidos precisam sair hoje, quantos já saíram e quantos estão atrasados."""
    return _shipment_cards(db, None)[1]


def kanban(db: Session, channel: str | None = None) -> list[KanbanColumn]:
    """Quadro da Visão geral, com filtro opcional por marketplace.

    Com conta real conectada: "Envios de hoje" (pelo prazo de despacho do marketplace).
    Na simulação: os pedidos fictícios do dia.
    """
    if channel not in (None, Channel.ML, Channel.SHOPEE, Channel.OTHER):
        channel = None
    if has_real_data(db):
        return _shipment_cards(db, channel)[0]
    start, end = local_day_bounds_utc(local_today())
    tax_rate = float(get_setting(db, "tax_rate_pct") or 0)
    columns = {status: KanbanColumn(status, KANBAN_LABELS[status]) for status in KANBAN_COLUMNS}
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
            Order.ordered_at >= y_start, Order.ordered_at < y_now,
            Order.kanban_status != KanbanStatus.CANCELLED,
        )
    )

    q_today = db.scalar(select(func.count()).select_from(Question).where(
        Question.asked_at >= start, Question.asked_at < end))
    q_answered = db.scalar(select(func.count()).select_from(Question).where(
        Question.asked_at >= start, Question.asked_at < end,
        Question.status.in_([QuestionStatus.ANSWERED, QuestionStatus.SIMULATED])))
    q_waiting = db.scalar(select(func.count()).select_from(Question).where(
        Question.status.in_([QuestionStatus.NEW, QuestionStatus.PROPOSED, QuestionStatus.HUMAN])))

    accounts = {a.provider: a for a in db.scalars(
        select(IntegrationAccount).order_by(IntegrationAccount.is_demo.desc()))}
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
        fees_missing=any(not o.fees_known for o in orders_today),
        tax_configured=tax_rate > 0,
        dispatch=dispatch_day(db) if has_real_data(db) else None,
    )
