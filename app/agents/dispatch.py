"""Alerta de despacho — pedidos do Mercado Livre com prazo de despacho vencendo ou vencido.

Só lê (prazo do SLA do Mercado Livre, guardado em `shipments`) e avisa.
Envios do Full ficam de fora: quem despacha é o Mercado Livre.
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.base import run_agent
from app.agents.config import agent_rules, has_real_data
from app.core.alerts import AlertSpec, sync_alerts
from app.core.db import utcnow
from app.core.timeutil import to_local
from app.integrations.mercadolivre.mapping import WAITING_DISPATCH
from app.models import Order, Shipment
from app.models.enums import AlertSeverity, Channel
from app.services.dashboard import dispatch_day, is_shipped

SLUG = "despacho"
MAX_ALERTS = 10


def _when(deadline) -> str:
    local = to_local(deadline)
    today = to_local(utcnow()).date()
    if local.date() == today:
        return f"hoje até {local:%H:%M}"
    if local.date() == today + timedelta(days=1):
        return f"amanhã até {local:%H:%M}"
    return f"até {local:%d/%m %H:%M}"


def build_alerts(db: Session, warn_hours: float) -> tuple[list[AlertSpec], int, int]:
    now = utcnow()
    late, soon = [], []
    rows = db.scalars(select(Shipment).where(
        Shipment.channel == Channel.ML, Shipment.status.in_(WAITING_DISPATCH),
        Shipment.dispatch_deadline.is_not(None)).order_by(Shipment.dispatch_deadline))
    for row in rows:
        if row.logistic_type == "fulfillment":
            continue
        order = db.scalar(select(Order).where(Order.channel == Channel.ML, Order.external_id == row.order_ref))
        if is_shipped(row, order):
            continue  # o Olist já marca como enviado: não é atraso
        label = f"Pedido {order.olist_number}" if order and order.olist_number else f"Pedido {row.order_ref}"
        if row.dispatch_deadline <= now:
            hours = int((now - row.dispatch_deadline).total_seconds() // 3600)
            late.append(AlertSpec(f"envio:{row.shipment_id}", AlertSeverity.CRITICAL,
                                  f"{label} (Mercado Livre): prazo de despacho venceu"
                                  + (f" há {hours} h" if hours else ""),
                                  "Despache o quanto antes: atraso no envio afeta a reputação.", "envio"))
        elif row.dispatch_deadline - now <= timedelta(hours=warn_hours):
            soon.append(AlertSpec(f"envio:{row.shipment_id}", AlertSeverity.WARNING,
                                  f"{label} (Mercado Livre): despachar {_when(row.dispatch_deadline)}",
                                  None, "envio"))
    specs = (late + soon)[:MAX_ALERTS]
    rest = len(late) + len(soon) - len(specs)
    if rest > 0:
        specs.append(AlertSpec("resumo", AlertSeverity.INFO, f"Mais {rest} pedido(s) para despachar",
                               "Confira no Mercado Livre ou no Olist.", "pagina"))
    return specs, len(late), len(soon)


def run_dispatch_alerts(session_factory: sessionmaker[Session]):
    with session_factory() as db:
        if not has_real_data(db):
            return None

        def work(db: Session, agent) -> tuple[int, str]:
            specs, late, soon = build_alerts(db, agent_rules(agent)["horas_aviso"])
            sync_alerts(db, SLUG, specs)
            db.commit()
            day = dispatch_day(db)
            parts = [f"{day.to_ship} para enviar hoje", f"{day.shipped} já enviado(s)"]
            if day.late:
                parts.append(f"{day.late} atrasado(s)")
            return day.to_ship + day.late, " · ".join(parts)

        return run_agent(db, SLUG, work)
