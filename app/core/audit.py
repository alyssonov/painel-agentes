"""Gravação no registro de auditoria (audit_log)."""

from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.audit import AuditLog

# Chaves que jamais podem aparecer nos detalhes da auditoria.
_FORBIDDEN_KEYS = {"password", "senha", "token", "access_token", "refresh_token", "secret"}


def record(
    db: Session,
    *,
    actor: str,
    event: str,
    target_type: str | None = None,
    target_id: str | int | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """Registra um evento. Não faz commit: quem chama decide o momento.

    actor: "user:<id>", "agent:<slug>" ou "system".
    event: nome curto com ponto, ex.: "login.ok", "acao.aprovada".
    """
    safe_details = {k: v for k, v in (details or {}).items() if k.lower() not in _FORBIDDEN_KEYS}
    entry = AuditLog(
        actor=actor,
        event=event,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        details=safe_details,
        dry_run=get_settings().dry_run,
    )
    db.add(entry)
    return entry
