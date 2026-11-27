"""Relatórios para baixar (planilha para o Excel) ou imprimir / salvar em PDF.

Todos usam a mesma conta do Lucro real (app/services/margin.py via order_margin):
preço − taxa do marketplace − frete pago pela loja − custo (Olist) − impostos.
Só leitura: nada é alterado. Só vendas reais (a loja de simulação fica de fora
quando há conta real conectada; sem conta real, mostra a simulação).
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.money import format_brl, format_pct
from app.core.timeutil import local_day_bounds_utc, local_today, to_local
from app.models import Order, OrderItem
from app.models.enums import CHANNEL_LABELS, Channel, KanbanStatus
from app.agents.config import has_real_data
from app.services.dashboard import money_confirmed, order_margin
from app.services.margin_report import margin_report
from app.services.store_settings import other_costs_cents

KINDS = {
    "vendas": ("Vendas do período", "Uma linha por venda: valor, taxa, frete, custo, imposto e lucro."),
    "produtos": ("Lucro por produto", "Unidades, faturamento, lucro e margem de cada produto."),
    "diario": ("Resumo por dia", "Pedidos, faturamento e lucro de cada dia, separado por marketplace."),
}
PERIODS = {"mes": "Este mês", "mes_passado": "Mês passado", "30": "Últimos 30 dias", "7": "Últimos 7 dias",
           "personalizado": "Escolher datas"}
MAX_DAYS = 400


@dataclass
class Period:
    start: date  # primeiro dia (fuso da loja)
    end: date  # último dia, inclusive

    @property
    def label(self) -> str:
        return f"{self.start:%d/%m/%Y} a {self.end:%d/%m/%Y}"

    @property
    def utc_bounds(self) -> tuple[datetime, datetime]:
        return local_day_bounds_utc(self.start)[0], local_day_bounds_utc(self.end)[1]


def parse_period(preset: str, de: str = "", ate: str = "", *, today: date | None = None) -> Period:
    """Período escolhido na tela. Datas inválidas viram "este mês"; no máximo MAX_DAYS dias."""
    today = today or local_today()
    if preset == "mes_passado":
        end = today.replace(day=1) - timedelta(days=1)
        return Period(end.replace(day=1), end)
    if preset in ("7", "30"):
        return Period(today - timedelta(days=int(preset) - 1), today)
    if preset == "personalizado":
        try:
            start, end = date.fromisoformat(de), date.fromisoformat(ate)
        except ValueError:
            start = end = None
        if start and end:
            start, end = min(start, end), max(start, end)
            return Period(max(start, end - timedelta(days=MAX_DAYS - 1)), end)
    return Period(today.replace(day=1), today)


@dataclass
class Table:
    title: str
    period: Period
    columns: list[str]
    rows: list[list] = field(default_factory=list)  # valores crus (int de centavos, float de %, texto)
    money: set[int] = field(default_factory=set)  # índices das colunas em centavos
    pct: set[int] = field(default_factory=set)  # índices das colunas em %
    totals: list | None = None
    notes: list[str] = field(default_factory=list)

    def display(self, row: list) -> list[str]:
        out = []
        for i, value in enumerate(row):
            if value is None or value == "":
                out.append("—")
            elif i in self.money:
                out.append(format_brl(int(value)))
            elif i in self.pct:
                out.append(format_pct(value))
            else:
                out.append(str(value))
        return out


def _cell_csv(table: Table, i: int, value) -> str:
    """Planilha brasileira: vírgula decimal, sem "R$" (o Excel entende como número)."""
    if value is None:
        return ""
    if i in table.money:
        return f"{int(value) / 100:.2f}".replace(".", ",")
    if i in table.pct:
        return f"{float(value):.1f}".replace(".", ",")
    return str(value)


def to_csv(table: Table) -> str:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow([f"{table.title} — {table.period.label}"])
    writer.writerow(table.columns)
    for row in table.rows:
        writer.writerow([_cell_csv(table, i, v) for i, v in enumerate(row)])
    if table.totals:
        writer.writerow([_cell_csv(table, i, v) for i, v in enumerate(table.totals)])
    for note in table.notes:
        writer.writerow([note])
    return "﻿" + out.getvalue()  # BOM: o Excel abre os acentos certos


def _orders(db: Session, period: Period, demo: bool) -> list[Order]:
    start, end = period.utc_bounds
    return list(db.scalars(select(Order).where(Order.ordered_at >= start, Order.ordered_at < end,
                                               Order.kanban_status != KanbanStatus.CANCELLED,
                                               Order.is_demo.is_(demo))
                           .options(selectinload(Order.items).selectinload(OrderItem.product))
                           .order_by(Order.ordered_at)))


def _channel(order: Order) -> str:
    try:
        return CHANNEL_LABELS[Channel(order.channel)]
    except ValueError:
        return order.channel


def build(db: Session, kind: str, period: Period, tax_rate_pct: float) -> Table:
    demo = not has_real_data(db)
    if kind == "produtos":
        return _products(db, period, tax_rate_pct, demo)
    if kind == "diario":
        return _daily(db, period, tax_rate_pct, demo)
    return _sales(db, period, tax_rate_pct, demo)


def _sales(db: Session, period: Period, tax: float, demo: bool) -> Table:
    t = Table(KINDS["vendas"][0], period,
              ["Data", "Marketplace", "Pedido", "Cliente", "Itens", "Faturamento", "Taxa do marketplace",
               "Frete pago pela loja", "Custo dos produtos", "Impostos", "Embalagem e outros", "Lucro", "Margem %",
               "Valores conferidos"],
              money={5, 6, 7, 8, 9, 10, 11}, pct={12})
    sums = [0] * 7
    pending = 0
    other = other_costs_cents(db)
    for o in _orders(db, period, demo):
        m = order_margin(o, tax, other)
        items = "; ".join(f"{i.quantity}x {i.title}" for i in o.items)[:300]
        confirmed = money_confirmed(o)
        pending += 0 if confirmed else 1
        values = [m.revenue_cents, m.fee_cents, m.shipping_cents, m.cost_cents, m.tax_cents, m.other_cents,
                  m.profit_cents]
        sums = [a + b for a, b in zip(sums, values)]
        t.rows.append([to_local(o.ordered_at).strftime("%d/%m/%Y %H:%M"), _channel(o), o.external_id,
                       o.customer_short_name, items, *values, m.margin_pct, "sim" if confirmed else "não"])
    t.totals = ["Total", "", f"{len(t.rows)} vendas", "", "", *sums,
                sums[6] / sums[0] * 100 if sums[0] else None, ""]
    if pending:
        t.notes.append(f"{pending} venda(s) ainda sem a taxa/valor recebido confirmados pelo marketplace: "
                       "o lucro delas é estimado.")
    return t


def _products(db: Session, period: Period, tax: float, demo: bool) -> Table:
    start, end = period.utc_bounds
    report = margin_report(db, start, end, tax, demo=demo)
    t = Table(KINDS["produtos"][0], period, ["SKU", "Produto", "Unidades", "Faturamento", "Lucro", "Margem %",
                                             "Observação"], money={3, 4}, pct={5})
    for p in sorted(report.products, key=lambda p: -p.revenue):
        note = "; ".join(n for n, flag in (("sem custo no Olist", p.missing_cost),
                                           ("taxa ainda não confirmada", p.missing_fees)) if flag)
        t.rows.append([p.sku, p.name, p.units, p.revenue, p.profit, p.margin_pct, note])
    tot = report.totals
    t.totals = ["Total", f"{len(t.rows)} produtos", sum(p.units for p in report.products), tot.revenue, tot.profit,
                tot.margin_pct, ""]
    if report.products_missing_cost:
        t.notes.append(f"{report.products_missing_cost} produto(s) sem custo no Olist: o lucro deles aparece maior "
                       "do que é.")
    return t


def _daily(db: Session, period: Period, tax: float, demo: bool) -> Table:
    t = Table(KINDS["diario"][0], period,
              ["Dia", "Pedidos", "Faturamento", "Lucro", "Margem %", "Mercado Livre", "Shopee", "Outros"],
              money={2, 3, 5, 6, 7}, pct={4})
    days: dict[date, list[int]] = {}
    other = other_costs_cents(db)
    for o in _orders(db, period, demo):
        m = order_margin(o, tax, other)
        row = days.setdefault(to_local(o.ordered_at).date(), [0, 0, 0, 0, 0, 0])
        row[0] += 1
        row[1] += m.revenue_cents
        row[2] += m.profit_cents
        slot = {Channel.ML: 3, Channel.SHOPEE: 4}.get(o.channel, 5)
        row[slot] += m.revenue_cents
    day = period.start
    total = [0] * 6
    while day <= period.end:
        v = days.get(day, [0] * 6)
        total = [a + b for a, b in zip(total, v)]
        t.rows.append([day.strftime("%d/%m/%Y"), v[0], v[1], v[2], v[2] / v[1] * 100 if v[1] else None,
                       v[3], v[4], v[5]])
        day += timedelta(days=1)
    t.totals = ["Total", total[0], total[1], total[2], total[2] / total[1] * 100 if total[1] else None,
                total[3], total[4], total[5]]
    return t
