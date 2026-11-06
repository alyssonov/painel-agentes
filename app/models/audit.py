"""Registro de auditoria: tudo que pessoas e agentes fizeram.

Esta tabela é SOMENTE DE INSERÇÃO. Alterar ou apagar registros é bloqueado
no código (ver os eventos abaixo) — o histórico não pode ser reescrito.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, event
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(80))  # "user:3", "agent:perguntas", "system"
    event: Mapped[str] = mapped_column(String(80), index=True)  # ex.: "login.ok"
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dry_run: Mapped[bool] = mapped_column(default=True)


class AuditLogImmutableError(RuntimeError):
    """Tentativa de alterar ou apagar um registro de auditoria."""


@event.listens_for(AuditLog, "before_update")
def _bloquear_update(*_args) -> None:
    raise AuditLogImmutableError("O registro de auditoria não pode ser alterado.")


@event.listens_for(AuditLog, "before_delete")
def _bloquear_delete(*_args) -> None:
    raise AuditLogImmutableError("O registro de auditoria não pode ser apagado.")
