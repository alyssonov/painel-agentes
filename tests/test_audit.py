"""O registro de auditoria só aceita inserção."""

import pytest
from sqlalchemy import select

from app.core import audit
from app.models import AuditLog
from app.models.audit import AuditLogImmutableError


def test_registra_evento_sem_segredos(db):
    audit.record(db, actor="user:1", event="teste", target_type="pedido", target_id=42,
                 details={"campo": "ok", "password": "x", "access_token": "y"})
    db.commit()
    entry = db.scalar(select(AuditLog))
    assert entry.target_id == "42"
    assert entry.details == {"campo": "ok"}
    assert entry.dry_run is True


def test_nao_permite_alterar(db):
    entry = audit.record(db, actor="system", event="teste")
    db.commit()
    entry.event = "alterado"
    with pytest.raises(AuditLogImmutableError):
        db.commit()


def test_nao_permite_apagar(db):
    entry = audit.record(db, actor="system", event="teste")
    db.commit()
    db.delete(entry)
    with pytest.raises(AuditLogImmutableError):
        db.commit()
