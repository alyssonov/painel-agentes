"""Agente 7 — Calculadora de margem real (sem IA, só cálculo).

Analisa as vendas dos últimos N dias e avisa:
- produtos vendidos com prejuízo;
- produtos com margem abaixo da mínima configurada;
- produtos vendidos sem custo cadastrado no Olist (a margem fica errada).
"""

from datetime import timedelta

from sqlalchemy.orm import Session, sessionmaker

from app.agents.base import run_agent
from app.agents.config import agent_rules, has_real_data
from app.core.alerts import AlertSpec, sync_alerts
from app.core.db import utcnow
from app.core.money import format_brl, format_pct
from app.models.enums import AlertSeverity
from app.services.margin_report import margin_report
from app.services.store_settings import get_setting

SLUG = "margem"
MAX_ALERTS = 6


def build_alerts(report, min_margin_pct: float, days: int) -> list[AlertSpec]:
    specs: list[AlertSpec] = []
    for p in report.products:
        if p.missing_cost:
            continue  # sem custo, a margem não é confiável (vai no aviso abaixo)
        if p.profit < 0:
            specs.append(AlertSpec(f"prejuizo:{p.key}", AlertSeverity.CRITICAL,
                                   f"{p.name} deu prejuízo nos últimos {days} dias",
                                   f"{p.units} vendida(s), resultado {format_brl(p.profit)}. "
                                   "Revise preço, custo ou frete.", "produto"))
        elif p.margin_pct is not None and p.margin_pct < min_margin_pct:
            specs.append(AlertSpec(f"margem_baixa:{p.key}", AlertSeverity.WARNING,
                                   f"{p.name} com margem de {format_pct(p.margin_pct)}",
                                   f"Abaixo da mínima de {format_pct(min_margin_pct, 0)} nos últimos {days} dias.",
                                   "produto"))
    specs = specs[:MAX_ALERTS]
    if report.products_missing_cost:
        specs.append(AlertSpec("sem_custo", AlertSeverity.WARNING,
                               f"{report.products_missing_cost} produto(s) vendido(s) sem custo no Olist",
                               "Cadastre o custo no Olist para a margem real ficar certa.", "produto"))
    return specs


def run_margin_agent(session_factory: sessionmaker[Session]):
    with session_factory() as db:
        if not has_real_data(db):
            return None

        def work(db: Session, agent) -> tuple[int, str]:
            rules = agent_rules(agent)
            days = int(rules["janela_dias"])
            now = utcnow()
            tax = float(get_setting(db, "tax_rate_pct") or 0)
            report = margin_report(db, now - timedelta(days=days), now, tax)
            sync_alerts(db, SLUG, build_alerts(report, rules["margem_minima_pct"], days))
            db.commit()
            losses = sum(1 for p in report.products if p.profit < 0 and not p.missing_cost)
            summary = f"Margem {days} dias: {format_pct(report.totals.margin_pct)}"
            if losses:
                summary += f" · {losses} produto(s) no prejuízo"
            return report.totals.orders, summary

        return run_agent(db, SLUG, work)
