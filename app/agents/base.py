"""Base dos agentes: registra cada execução (agent_runs) com o resumo do dia."""

import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models import Agent, AgentRun

logger = logging.getLogger("agentes")


def run_agent(db: Session, slug: str, work: Callable[[Session, Agent], tuple[int, str]]) -> AgentRun | None:
    """Executa `work` se o agente estiver ativo. work devolve (itens processados, resumo)."""
    agent = db.scalar(select(Agent).where(Agent.slug == slug))
    if agent is None or not agent.enabled:
        return None
    run = AgentRun(agent_id=agent.id, started_at=utcnow(), status="rodando")
    db.add(run)
    db.commit()
    try:
        items, summary = work(db, agent)
        run.status, run.items_processed, run.summary = "ok", items, summary
    except Exception as exc:  # noqa: BLE001 - erro do agente vira status no painel, nunca derruba o worker
        db.rollback()
        logger.exception("Erro no agente %s", slug)
        run = db.merge(run)
        run.status, run.error = "erro", str(exc)[:500]
        run.summary = str(exc)[:190]
    run.finished_at = utcnow()
    db.commit()
    return run
