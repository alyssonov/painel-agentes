"""Roda as automações: junta os alertas novos e manda UM e-mail com tudo.

Regras:
- só alertas reais (nunca os da loja de simulação) e abertos;
- só alertas criados DEPOIS que a automação foi ligada (ligar não dispara uma enxurrada);
- cada alerta é avisado uma vez só, mesmo que combine com mais de uma automação;
- no horário silencioso nada sai: os avisos esperam e vão juntos quando o horário acaba;
- se o e-mail falhar, nada é marcado como avisado (tenta de novo na próxima rodada).

São mensagens internas para a loja (como o relatório diário): saem também no modo
simulação e ficam registradas em `reports` e no `audit_log` (regra 4 do CLAUDE.md).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.automations.catalog import AUTOMATIONS, BY_SLUG, QUESTIONS_SOURCE, SEVERITY_ORDER, default_params
from app.core import audit
from app.core.alerts import AlertSpec, sync_alerts
from app.core.db import utcnow
from app.core.timeutil import to_local
from app.models import Alert, Automation, AutomationDelivery, Question, Report
from app.models.enums import AlertSeverity, AlertStatus, QuestionStatus
from app.notify.email import EmailError, parse_recipients, send_email
from app.services.store_settings import get_setting

logger = logging.getLogger("painel.automacoes")

QUIET_KEY = "automation_quiet"  # {"ativo": bool, "de": 22, "ate": 7} (horas no fuso da loja)
RECIPIENTS_KEY = "automation_recipients"  # vazio = os mesmos do relatório diário
QUIET_DEFAULT = {"ativo": True, "de": 22, "ate": 7}
WAITING = (QuestionStatus.NEW, QuestionStatus.PROPOSED, QuestionStatus.HUMAN)
SEVERITY_LABEL = {AlertSeverity.CRITICAL: "Urgente", AlertSeverity.WARNING: "Atenção", AlertSeverity.INFO: "Info"}

Sender = Callable[..., None]


@dataclass
class RunResult:
    sent: int = 0  # alertas avisados
    waiting_quiet: bool = False
    error: str | None = None
    by_automation: dict[str, int] = field(default_factory=dict)


def ensure_rows(db: Session) -> dict[str, Automation]:
    """Uma linha por automação do catálogo (criada desligada na primeira vez)."""
    rows = {a.slug: a for a in db.scalars(select(Automation))}
    for defn in AUTOMATIONS:
        if defn.slug not in rows:
            rows[defn.slug] = Automation(slug=defn.slug, enabled=False, params=default_params(defn))
            db.add(rows[defn.slug])
    db.flush()
    return rows


def quiet_settings(db: Session) -> dict:
    return {**QUIET_DEFAULT, **(get_setting(db, QUIET_KEY) or {})}


def is_quiet(settings: dict, now_utc: datetime) -> bool:
    """Agora está no horário silencioso? (ex.: das 22h às 7h, atravessando a meia-noite)."""
    if not settings.get("ativo"):
        return False
    hour, start, end = to_local(now_utc).hour, int(settings["de"]), int(settings["ate"])
    if start == end:
        return False
    return start <= hour < end if start < end else (hour >= start or hour < end)


def recipients(db: Session) -> list[str]:
    return parse_recipients(get_setting(db, RECIPIENTS_KEY) or get_setting(db, "report_recipients") or "")


def refresh_question_alerts(db: Session, rows: dict[str, Automation], now: datetime) -> None:
    """Alertas de perguntas esperando há mais de N horas (só enquanto a automação estiver ligada)."""
    row = rows["perguntas"]
    specs: list[AlertSpec] = []
    if row.enabled:
        hours = int((row.params or {}).get("horas") or 2)
        limit = now - timedelta(hours=hours)
        for q in db.scalars(select(Question).where(Question.status.in_(WAITING), Question.asked_at <= limit,
                                                   Question.is_demo.is_(False)).order_by(Question.asked_at)):
            waited = int((now - q.asked_at).total_seconds() // 3600)
            title = (q.listing_title or "anúncio")[:80]
            specs.append(AlertSpec(f"pergunta:{q.id}", AlertSeverity.WARNING,
                                   f"Pergunta esperando resposta há {waited} h", f"{title}: “{q.text[:140]}”",
                                   target_type="pergunta"))
    sync_alerts(db, QUESTIONS_SOURCE, specs)


def _matches(alert: Alert, row: Automation) -> bool:
    defn = BY_SLUG[row.slug]
    if defn.sources is not None and alert.source not in defn.sources:
        return False
    minimum = SEVERITY_ORDER.get(defn.severity_for(row.params or {}), 0)
    return SEVERITY_ORDER.get(alert.severity, 0) >= minimum and bool(row.enabled_at and alert.created_at >= row.enabled_at)


def pending_alerts(db: Session, rows: dict[str, Automation]) -> dict[str, list[Alert]]:
    """Alertas novos, ainda não avisados, agrupados pela automação que vai avisar (sem repetir)."""
    enabled = [r for r in rows.values() if r.enabled]
    if not enabled:
        return {}
    done = set(db.scalars(select(AutomationDelivery.alert_id)))
    taken: set[int] = set()
    groups: dict[str, list[Alert]] = {}
    alerts = list(db.scalars(select(Alert).where(Alert.status == AlertStatus.OPEN, Alert.is_demo.is_(False))
                             .order_by(Alert.created_at)))
    # As automações específicas primeiro; "Algo urgente" pega só o que sobrar.
    for row in sorted(enabled, key=lambda r: BY_SLUG[r.slug].sources is None):
        for alert in alerts:
            if alert.id not in done and alert.id not in taken and _matches(alert, row):
                groups.setdefault(row.slug, []).append(alert)
                taken.add(alert.id)
    return groups


def build_email(groups: dict[str, list[Alert]]) -> tuple[str, str, str]:
    total = sum(len(v) for v in groups.values())
    names = [BY_SLUG[s].name for s in groups]
    subject = f"Gerente do Ano: {total} aviso{'s' if total > 1 else ''} — " + ", ".join(names)[:120]
    lines, parts = [], []
    for slug, alerts in groups.items():
        lines.append(f"\n{BY_SLUG[slug].name}")
        parts.append(f'<h3 style="margin:18px 0 6px;font:600 15px sans-serif;color:#111">{escape(BY_SLUG[slug].name)}</h3><ul>')
        for a in alerts:
            label = SEVERITY_LABEL.get(a.severity, "")
            lines.append(f"- [{label}] {a.title}" + (f" — {a.detail}" if a.detail else ""))
            parts.append(f'<li style="margin:4px 0;font:14px sans-serif;color:#222"><strong>{escape(label)}:</strong> '
                         f'{escape(a.title)}' + (f'<br><span style="color:#555">{escape(a.detail)}</span>' if a.detail else "")
                         + "</li>")
        parts.append("</ul>")
    footer = ("Aviso automático do Gerente do Ano. Abra o painel para ver os detalhes. "
              "Para mudar o que chega por e-mail: Inteligência → Automações.")
    text = "\n".join(lines).strip() + "\n\n" + footer
    html = "".join(parts) + f'<p style="font:12px sans-serif;color:#666;margin-top:20px">{escape(footer)}</p>'
    return subject, text, html


def _record_report(db: Session, now: datetime, count: int, to: list[str], error: str | None) -> None:
    """Um registro por dia em `reports` (kind "avisos"), somando os avisos do dia."""
    today = to_local(now).date()
    row = db.scalar(select(Report).where(Report.kind == "avisos", Report.period_start == today))
    if row is None:
        row = Report(kind="avisos", period_start=today, period_end=today, content={"avisos": 0}, status="pendente")
        db.add(row)
    if error:
        row.status, row.error = "falhou", error[:500]
    else:
        row.content = {"avisos": int((row.content or {}).get("avisos", 0)) + count}
        row.status, row.error, row.sent_at, row.sent_to = "enviado", None, now, ", ".join(to)


def run_automations(session_factory: sessionmaker[Session], *, now: datetime | None = None,
                    sender: Sender = send_email) -> RunResult:
    now = now or utcnow()
    result = RunResult()
    with session_factory() as db:
        rows = ensure_rows(db)
        refresh_question_alerts(db, rows, now)
        db.commit()
        groups = pending_alerts(db, rows)
        if not groups:
            return result
        if is_quiet(quiet_settings(db), now):
            result.waiting_quiet = True
            return result
        to = recipients(db)
        subject, text, html = build_email(groups)
        count = sum(len(v) for v in groups.values())
        try:
            if not to:
                raise EmailError("Ninguém cadastrado para receber os avisos (Automações → Quem recebe).")
            sender(to, subject, text, html)
        except EmailError as exc:
            result.error = str(exc)
            for slug in groups:
                rows[slug].last_error = str(exc)[:500]
            _record_report(db, now, count, to, str(exc))
            audit.record(db, actor="system", event="automacao.falhou", details={"erro": str(exc)[:200]})
            db.commit()
            logger.warning("Automações: aviso não enviado: %s", exc)
            return result
        for slug, alerts in groups.items():
            row = rows[slug]
            for alert in alerts:
                db.add(AutomationDelivery(automation_id=row.id, alert_id=alert.id, sent_at=now))
            row.last_sent_at, row.last_error = now, None
            result.by_automation[slug] = len(alerts)
        result.sent = count
        _record_report(db, now, count, to, None)
        audit.record(db, actor="system", event="automacao.enviada",
                     details={"avisos": count, "automacoes": list(groups), "destinatarios": len(to)})
        db.commit()
    return result
