"""Agente 3 — Vigia de estoque (somente leitura do Olist; não altera estoque).

- Calcula a média de vendas por dia e prevê quando cada produto acaba.
- Avisa produto esgotado (que vendia) e estoque baixo.
- Avisa divergência entre o estoque do Olist e o mostrado no anúncio
  (anúncios do Mercado Livre Full ficam de fora: o estoque está no armazém deles).
"""

from sqlalchemy.orm import Session, sessionmaker

from app.agents.base import run_agent
from app.agents.config import agent_rules, has_real_data
from app.core.alerts import AlertSpec, sync_alerts
from app.models.enums import AlertSeverity
from app.services.stock import stock_rows

SLUG = "estoque"
MAX_ALERTS = 8  # alertas individuais; o resto vira um resumo (para não poluir a Visão geral)


def _num(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _days(value: float) -> str:
    days = max(1, round(value))
    return f"{days} dia" + ("s" if days > 1 else "")


def build_alerts(rows, max_alerts: int = MAX_ALERTS) -> tuple[list[AlertSpec], int, int]:
    urgent, low, divergent = [], [], []
    for row in rows:
        if row.status == "esgotado":
            urgent.append(AlertSpec(f"esgotado:{row.product_id}", AlertSeverity.CRITICAL,
                                    f"{row.name} está esgotado no Olist",
                                    f"Vendia cerca de {_num(row.avg_daily)} por dia.", "produto"))
        elif row.status == "baixo":
            low.append(AlertSpec(f"baixo:{row.product_id}", AlertSeverity.WARNING,
                                 f"{row.name} acaba em cerca de {_days(row.days_left)}",
                                 f"Restam {row.available} unidade(s). Média de "
                                 f"{_num(row.avg_daily)} vendas por dia.", "produto"))
        if row.diverges:
            listing = next(item for item in row.listings if item.diverges)
            divergent.append(AlertSpec(f"divergencia:{row.product_id}", AlertSeverity.WARNING,
                                       f"{row.name}: estoque do Olist ({row.available}) diferente do "
                                       f"{listing.channel_label} ({listing.quantity})",
                                       "Confira a sincronização de estoque do Olist com o anúncio.", "produto"))
    ordered = urgent + low + divergent
    specs = ordered[:max_alerts]
    rest = len(ordered) - len(specs)
    if rest > 0:
        specs.append(AlertSpec("resumo", AlertSeverity.INFO, f"Mais {rest} aviso(s) de estoque",
                               "Veja a lista completa em Estoque e preços.", "pagina"))
    return specs, len(urgent) + len(low), len(divergent)


def run_stock_watch(session_factory: sessionmaker[Session]):
    with session_factory() as db:
        if not has_real_data(db):
            return None

        def work(db: Session, agent) -> tuple[int, str]:
            rules = agent_rules(agent)
            rows = stock_rows(db, window_days=int(rules["janela_vendas_dias"]),
                              alert_days=rules["dias_alerta"], tolerance=int(rules["tolerancia_divergencia"]))
            specs, low, divergent = build_alerts(rows)
            sync_alerts(db, SLUG, specs)
            db.commit()
            parts = [f"{low} produto(s) com estoque baixo ou esgotado" if low else "Estoque em dia"]
            if divergent:
                parts.append(f"{divergent} divergência(s)")
            return len(rows), " · ".join(parts)

        return run_agent(db, SLUG, work)
