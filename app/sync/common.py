"""Partes comuns das sincronizações: controle de execução, falhas e alertas."""

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core import audit
from app.core.db import utcnow
from app.integrations.errors import AuthExpired
from app.integrations.oauth import get_real_account
from app.models import Alert, SyncRun
from app.models.enums import PROVIDER_LABELS, AlertSeverity, AlertStatus, IntegrationStatus, SyncStatus

# Uma sincronização "rodando" há mais que isto é considerada travada.
STALE_RUN = timedelta(minutes=30)


class SyncAlreadyRunning(Exception):
    """Outra sincronização da mesma integração já está em andamento."""


def start_run(db: Session, provider: str) -> SyncRun:
    """Registra o início. Impede duas sincronizações da mesma integração ao mesmo tempo."""
    running = db.scalar(select(SyncRun).where(
        SyncRun.provider == provider, SyncRun.status == SyncStatus.RUNNING,
        SyncRun.started_at > utcnow() - STALE_RUN))
    if running:
        raise SyncAlreadyRunning()
    # Execuções antigas que ficaram "rodando" (ex.: computador desligou no meio).
    db.execute(update(SyncRun).where(
        SyncRun.provider == provider, SyncRun.status == SyncStatus.RUNNING
    ).values(status=SyncStatus.ERROR, error="Interrompida", finished_at=utcnow()))
    run = SyncRun(provider=provider, status=SyncStatus.RUNNING, counts={})
    db.add(run)
    db.commit()
    return run


def finish_run(session_factory: sessionmaker[Session], run_id: int, provider: str, status: str,
               counts: dict, error: str | None) -> SyncRun:
    with session_factory() as db:
        run = db.get(SyncRun, run_id)
        run.status, run.error, run.counts, run.finished_at = status, error, counts, utcnow()
        audit.record(db, actor="system", event=f"{provider}.sincronizacao.{status}",
                     target_type="sync_run", target_id=run.id, details={**counts, "erro": error})
        db.commit()
        return run


def record_failure(session_factory: sessionmaker[Session], provider: str, exc: Exception) -> None:
    """Marca a conta com erro e abre um alerta (um só, sem repetir a cada tentativa)."""
    label = PROVIDER_LABELS.get(provider, provider)
    with session_factory() as db:
        account = get_real_account(db, provider)
        if account is not None:
            account.status = IntegrationStatus.ERROR
            account.last_error = str(exc)
        already_open = db.scalar(select(Alert).where(Alert.source == provider, Alert.status == AlertStatus.OPEN))
        title = (f"{label} desconectado: é preciso autorizar de novo" if isinstance(exc, AuthExpired)
                 else f"Não foi possível atualizar os dados do {label}")
        if already_open:
            already_open.title, already_open.detail = title, str(exc)
        else:
            db.add(Alert(source=provider, severity=AlertSeverity.CRITICAL, title=title,
                         detail=str(exc), target_type="integracao", target_id=provider))
        db.commit()


def mark_ok(db: Session, account) -> None:
    """Conta voltou a funcionar: status OK e alertas da integração resolvidos."""
    account.status = IntegrationStatus.OK
    account.last_ok_at = utcnow()
    account.last_error = None
    for alert in db.scalars(select(Alert).where(Alert.source == account.provider,
                                                Alert.status == AlertStatus.OPEN)):
        alert.status = AlertStatus.RESOLVED
        alert.resolved_at = utcnow()


def get_cursor(account, key: str) -> datetime | None:
    value = (account.extra or {}).get(key)
    return datetime.fromisoformat(value) if value else None


def set_cursor(account, key: str, value: datetime) -> None:
    account.extra = {**(account.extra or {}), key: value.isoformat()}


def describe_unexpected(exc: Exception) -> str:
    """Resumo curto de um erro inesperado para mostrar no painel (tipo + onde aconteceu).

    Não inclui valores de variáveis nem tokens: só o tipo do erro, a mensagem
    curta e o arquivo/linha do código onde aconteceu — o suficiente para o suporte.
    """
    import traceback

    frames = [f for f in traceback.extract_tb(exc.__traceback__) if "/app/" in f.filename.replace("\\", "/")]
    where = f" em {frames[-1].filename.replace(chr(92), '/').split('/app/')[-1]}:{frames[-1].lineno}" if frames else ""
    message = str(exc).split("\n")[0][:160]
    return f"{type(exc).__name__}{where} — {message}"
