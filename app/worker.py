"""Processo dos agentes (agendador). Inicie com `python tarefas.py worker`.

O `python tarefas.py iniciar` (e o "Iniciar painel.bat") já liga este processo
junto com o painel.

Tarefas agendadas:
- Fase 1: no modo simulação, mantém os dados fictícios do dia em dia.
- Fase 2: lê o Olist (somente leitura) a cada OLIST_SYNC_MINUTES minutos.
- Fase 3: lê o Mercado Livre (perguntas, reputação, taxas) a cada ML_SYNC_MINUTES
  e roda o Respondedor de perguntas a cada AGENT_QUESTIONS_MINUTES.
"""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from app.agents.questions import run_questions_agent

from app.config import get_settings
from app.core.db import get_sessionmaker
from app.fake_data.seed import refresh_demo_if_stale
from app.sync.ml_sync import run_ml_sync
from app.sync.olist_sync import run_olist_sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker")


def job_refresh_demo() -> None:
    """No modo simulação, gera os dados de 'hoje' quando o dia vira."""
    with get_sessionmaker()() as db:
        if refresh_demo_if_stale(db):
            logger.info("Dados de simulação do dia gerados.")


def job_sync_olist() -> None:
    """Lê novidades do Olist. Não faz nada se o Olist não estiver conectado.

    Também mantém a autorização viva: cada leitura renova o token quando preciso
    (o refresh token do Olist vale só 1 dia).
    """
    run = run_olist_sync(get_sessionmaker())
    if run is not None:
        logger.info("Olist: sincronização %s %s", run.status, run.counts)


def job_sync_ml() -> None:
    """Lê perguntas, reputação e taxas do Mercado Livre (nada é respondido aqui)."""
    run = run_ml_sync(get_sessionmaker())
    if run is not None:
        logger.info("Mercado Livre: sincronização %s %s", run.status, run.counts)


def job_agent_questions() -> None:
    """Agente 1: gera sugestões de resposta (nada é enviado sem aprovação)."""
    run = run_questions_agent(get_sessionmaker())
    if run is not None:
        logger.info("Respondedor de perguntas: %s — %s", run.status, run.summary)


def main() -> None:
    settings = get_settings()
    settings.check_ready()
    scheduler = BlockingScheduler(timezone=settings.tz)
    if settings.dry_run:
        scheduler.add_job(job_refresh_demo, "interval", minutes=15, id="refresh_demo",
                          max_instances=1, coalesce=True)
        job_refresh_demo()
    scheduler.add_job(job_sync_olist, "interval", minutes=settings.olist_sync_minutes, id="sync_olist",
                      max_instances=1, coalesce=True)
    scheduler.add_job(job_sync_ml, "interval", minutes=settings.ml_sync_minutes, id="sync_ml",
                      max_instances=1, coalesce=True)
    scheduler.add_job(job_agent_questions, "interval", minutes=settings.agent_questions_minutes,
                      id="agent_questions", max_instances=1, coalesce=True)
    logger.info("Worker iniciado (DRY_RUN=%s). Ctrl+C para parar.", settings.dry_run)
    try:
        job_sync_olist()
        job_sync_ml()
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker parado.")


if __name__ == "__main__":
    main()
