"""Regras configuráveis de cada agente (editáveis na página Agentes)."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Agent, IntegrationAccount


@dataclass(frozen=True)
class Rule:
    key: str
    label: str
    default: float
    minimum: float
    maximum: float
    suffix: str = ""


RULES: dict[str, list[Rule]] = {
    "estoque": [
        Rule("dias_alerta", "Avisar quando o estoque durar menos de", 7, 1, 60, "dias"),
        Rule("janela_vendas_dias", "Calcular a média de vendas dos últimos", 30, 7, 90, "dias"),
        Rule("tolerancia_divergencia", "Diferença aceita entre Olist e anúncio", 0, 0, 50, "unidades"),
        Rule("prazo_fornecedor_dias", "Sugestão de compra: o fornecedor leva para entregar", 10, 1, 90, "dias"),
        Rule("cobertura_dias", "Sugestão de compra: comprar o suficiente para vender por", 30, 7, 180, "dias"),
    ],
    "nfe": [
        Rule("horas_sem_nota", "Avisar pedido parado sem nota há mais de", 24, 1, 120, "horas"),
    ],
    "despacho": [
        Rule("horas_aviso", "Avisar quando faltar menos de", 24, 1, 72, "horas para o prazo"),
    ],
    "reputacao": [
        Rule("limite_reclamacoes", "Limite do verde para reclamações", 2, 0.1, 20, "%"),
        Rule("limite_atrasos", "Limite do verde para atrasos no envio", 10, 0.5, 40, "%"),
        Rule("limite_cancelamentos", "Limite do verde para cancelamentos", 1.5, 0.1, 20, "%"),
        Rule("aviso_pct", "Avisar ao chegar a esta parte do limite", 80, 30, 100, "% do limite"),
    ],
    "relatorio": [
        Rule("hora_envio", "Enviar o relatório diário a partir das", 19, 0, 23, "horas"),
    ],
    "margem": [
        Rule("janela_dias", "Analisar as vendas dos últimos", 7, 1, 60, "dias"),
        Rule("margem_minima_pct", "Avisar produtos com margem abaixo de", 5, -50, 80, "%"),
    ],
}


def agent_rules(agent: Agent) -> dict[str, float]:
    """Regras do agente, com os valores padrão para o que não foi configurado."""
    saved = agent.config or {}
    return {rule.key: float(saved.get(rule.key, rule.default)) for rule in RULES.get(agent.slug, [])}


def has_real_data(db: Session) -> bool:
    """Há alguma conta real conectada? Sem isso (simulação), os agentes não mexem nos alertas."""
    return bool(db.scalar(select(func.count()).select_from(IntegrationAccount).where(
        IntegrationAccount.is_demo.is_(False), IntegrationAccount.access_token_enc.is_not(None))))
