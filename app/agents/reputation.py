"""Termômetro da reputação — Mercado Livre.

Compara as taxas de reclamações, atrasos e cancelamentos (lidas de /users/me)
com o limite do "verde" configurado e avisa ANTES de passar dele.

Os limites são configuráveis porque a documentação oficial traz tabelas
diferentes; os padrões seguem a tabela marcada como Brasil (MLB):
https://developers.mercadolivre.com.br/pt-br/reputacao-de-vendedores (consultado em 10/11/2026)
"""

from sqlalchemy.orm import Session, sessionmaker

from app.agents.base import run_agent
from app.agents.config import agent_rules, has_real_data
from app.core.alerts import AlertSpec, sync_alerts
from app.core.money import format_pct
from app.integrations.mercadolivre.auth import get_ml_account
from app.models.enums import AlertSeverity

SLUG = "reputacao"
METRICS = [("reclamacoes", "Reclamações", "limite_reclamacoes"),
           ("atrasos", "Atrasos no envio", "limite_atrasos"),
           ("cancelamentos", "Cancelamentos", "limite_cancelamentos")]


def build_alerts(metrics: dict, rules: dict) -> tuple[list[AlertSpec], list[str]]:
    specs, parts = [], []
    period = metrics.get("periodo")
    when = f" (últimos {period.replace('days', 'dias')})" if isinstance(period, str) else ""
    for key, label, rule_key in METRICS:
        rate = metrics.get(key)
        if rate is None:
            continue
        limit = rules[rule_key]
        parts.append(f"{label} {format_pct(rate, 2)}")
        if rate >= limit:
            specs.append(AlertSpec(key, AlertSeverity.CRITICAL,
                                   f"{label} no Mercado Livre em {format_pct(rate, 2)}: acima do limite do verde",
                                   f"Limite configurado: {format_pct(limit, 1)}{when}. A cor da reputação pode cair.",
                                   "reputacao"))
        elif rate >= limit * rules["aviso_pct"] / 100:
            specs.append(AlertSpec(key, AlertSeverity.WARNING,
                                   f"{label} no Mercado Livre em {format_pct(rate, 2)}: perto do limite do verde",
                                   f"Limite configurado: {format_pct(limit, 1)}{when}.", "reputacao"))
    return specs, parts


def run_reputation_check(session_factory: sessionmaker[Session]):
    with session_factory() as db:
        if not has_real_data(db):
            return None
        account = get_ml_account(db)
        if account is None or not account.access_token_enc:
            return None

        def work(db: Session, agent) -> tuple[int, str]:
            metrics = (get_ml_account(db).extra or {}).get("reputation_metrics") or {}
            specs, parts = build_alerts(metrics, agent_rules(agent))
            sync_alerts(db, SLUG, specs)
            db.commit()
            if not parts:
                return 0, "Aguardando os indicadores do Mercado Livre"
            return len(parts), " · ".join(parts)

        return run_agent(db, SLUG, work)
