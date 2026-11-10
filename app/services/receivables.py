"""Previsão de recebimentos: quanto dinheiro das vendas fica disponível em cada dia.

Usa a data de liberação e o valor líquido informados pelo Mercado Pago
(tabela `payments`). Sem IA (custo zero). Datas podem mudar (entrega atrasada,
reclamação, estorno) — o painel confere de novo periodicamente.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.timeutil import local_today, to_local
from app.models import Payment


@dataclass
class ReceivableDay:
    day: date
    payments: int
    net_cents: int


@dataclass
class Forecast:
    days: list[ReceivableDay] = field(default_factory=list)
    next_7_cents: int = 0
    next_30_cents: int = 0
    pending_total_cents: int = 0
    overdue_cents: int = 0  # data já passou e o Mercado Pago ainda não liberou
    overdue_count: int = 0
    released_7_cents: int = 0
    reading: int = 0  # pagamentos aprovados ainda sem data (sendo lidos no Mercado Pago)

    @property
    def max_day_cents(self) -> int:
        return max((d.net_cents for d in self.days), default=0)


def forecast(db: Session, *, horizon_days: int = 30, demo: bool = False) -> Forecast:
    now = utcnow()
    today = local_today()
    result = Forecast()
    base = (Payment.is_demo.is_(demo), Payment.status == "approved")
    by_day: dict[date, ReceivableDay] = {}
    for row in db.scalars(select(Payment).where(*base, Payment.release_date.is_not(None),
                                                Payment.release_status != "released")):
        net = row.net_cents if row.net_cents is not None else row.amount_cents
        result.pending_total_cents += net
        day = to_local(row.release_date).date()
        if row.release_date < now and day < today:
            result.overdue_cents += net
            result.overdue_count += 1
            continue
        if day <= today + timedelta(days=6):
            result.next_7_cents += net
        if day <= today + timedelta(days=horizon_days - 1):
            result.next_30_cents += net
            entry = by_day.setdefault(day, ReceivableDay(day, 0, 0))
            entry.payments += 1
            entry.net_cents += net
    result.days = sorted(by_day.values(), key=lambda d: d.day)
    result.released_7_cents = int(db.scalar(select(func.coalesce(func.sum(Payment.net_cents), 0)).where(
        *base, Payment.release_status == "released", Payment.release_date >= now - timedelta(days=7))) or 0)
    result.reading = int(db.scalar(select(func.count()).select_from(Payment).where(
        *base, Payment.release_date.is_(None))) or 0)
    return result
