"""Processo dos agentes (agendador). Inicie com `make worker`.

Fase 1: só mantém os dados de simulação do dia em dia. Os agentes de verdade
são registrados aqui a partir da Fase 3.
"""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from app.config import get_settings
from app.core.db import get_sessionmaker
from app.fake_data.seed import refresh_demo_if_stale

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker")


def job_refresh_demo() -> None:
    """No modo simulação, gera os dados de 'hoje' quando o dia vira."""
    with get_sessionmaker()() as db:
        if refresh_demo_if_stale(db):
            logger.info("Dados de simulação do dia gerados.")


def main() -> None:
    settings = get_settings()
    settings.check_ready()
    scheduler = BlockingScheduler(timezone=settings.tz)
    if settings.dry_run:
        scheduler.add_job(job_refresh_demo, "interval", minutes=15, id="refresh_demo",
                          max_instances=1, coalesce=True)
        job_refresh_demo()
    logger.info("Worker iniciado (DRY_RUN=%s). Ctrl+C para parar.", settings.dry_run)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker parado.")


if __name__ == "__main__":
    main()
