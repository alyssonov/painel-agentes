"""Alertas da faixa "O que pede atenção".

Os agentes não criam alertas soltos: a cada rodada dizem QUAIS alertas valem
agora (por chave). `sync_alerts` cria os novos, atualiza os que continuam e
resolve sozinho os que deixaram de valer — sem duplicar nem acumular.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models import Alert
from app.models.enums import AlertStatus


@dataclass(frozen=True)
class AlertSpec:
    key: str  # identifica o alerta dentro do agente (ex.: "estoque_baixo:123")
    severity: str  # AlertSeverity
    title: str
    detail: str | None = None
    target_type: str | None = None


def sync_alerts(db: Session, source: str, specs: list[AlertSpec]) -> dict[str, int]:
    """Deixa os alertas abertos de `source` iguais a `specs`. Retorna quantos foram criados/resolvidos."""
    wanted = {spec.key: spec for spec in specs}
    created = resolved = 0
    open_alerts = {a.target_id: a for a in db.scalars(select(Alert).where(
        Alert.source == source, Alert.status == AlertStatus.OPEN, Alert.is_demo.is_(False)))}
    for key, alert in open_alerts.items():
        spec = wanted.get(key)
        if spec is None:
            alert.status, alert.resolved_at = AlertStatus.RESOLVED, utcnow()
            resolved += 1
        else:
            alert.severity, alert.title, alert.detail = spec.severity, spec.title, spec.detail
    for key, spec in wanted.items():
        if key not in open_alerts:
            db.add(Alert(source=source, severity=spec.severity, title=spec.title, detail=spec.detail,
                         target_type=spec.target_type, target_id=key))
            created += 1
    return {"criados": created, "resolvidos": resolved}
