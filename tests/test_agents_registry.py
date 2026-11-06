"""Registro dos agentes."""

from sqlalchemy import select

from app.agents.registry import AGENTS, ensure_agents
from app.models import Agent
from app.models.enums import AgentMode


def test_cria_os_nove_agentes_pausados_e_manuais(db):
    ensure_agents(db)
    agents = list(db.scalars(select(Agent)))
    assert len(agents) == len(AGENTS) == 9
    assert all(not a.enabled and a.mode == AgentMode.MANUAL for a in agents)


def test_nao_sobrescreve_configuracao(db):
    ensure_agents(db)
    agent = db.scalar(select(Agent).where(Agent.slug == "perguntas"))
    agent.mode = AgentMode.AUTO
    db.commit()
    ensure_agents(db)
    db.refresh(agent)
    assert agent.mode == AgentMode.AUTO
