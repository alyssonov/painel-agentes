"""Tela Automações: escolher quais avisos chegam por e-mail, para quem e em que horário."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.automations.catalog import AUTOMATIONS, BY_SLUG, clean_params
from app.automations.runner import (
    QUIET_KEY,
    RECIPIENTS_KEY,
    build_email,
    ensure_rows,
    pending_alerts,
    quiet_settings,
    recipients,
)
from app.config import get_settings
from app.core import audit
from app.core.db import get_db, utcnow
from app.core.timeutil import to_local
from app.models import Alert, AutomationDelivery
from app.models.user import User
from app.notify.email import EmailError, invalid_recipients, parse_recipients, send_email
from app.services.store_settings import get_setting, set_setting
from app.web.auth import current_user, verify_csrf
from app.web.templating import flash, render

router = APIRouter(prefix="/automacoes")


def _when(dt) -> str | None:
    return to_local(dt).strftime("%d/%m às %H:%M") if dt else None


@router.get("")
def automations_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = ensure_rows(db)
    db.commit()
    waiting = pending_alerts(db, rows)
    cards = [{"defn": d, "row": rows[d.slug], "last_sent": _when(rows[d.slug].last_sent_at),
              "waiting": len(waiting.get(d.slug, []))} for d in AUTOMATIONS]
    recent = db.execute(select(AutomationDelivery, Alert).join(Alert, Alert.id == AutomationDelivery.alert_id)
                        .order_by(AutomationDelivery.sent_at.desc()).limit(15)).all()
    return render(request, "automations.html", {
        "cards": cards,
        "quiet": quiet_settings(db),
        "own_recipients": get_setting(db, RECIPIENTS_KEY) or "",
        "report_recipients": get_setting(db, "report_recipients") or "",
        "effective": recipients(db),
        "email_configured": get_settings().email_configured,
        "recent": [{"when": _when(d.sent_at), "automation": BY_SLUG[slug].name if (slug := _slug(rows, d)) else "—",
                    "title": a.title} for d, a in recent],
        "hours": list(range(24)),
    }, db=db)


def _slug(rows, delivery) -> str | None:
    for slug, row in rows.items():
        if row.id == delivery.automation_id:
            return slug
    return None


@router.post("/{slug}/ativo", dependencies=[Depends(verify_csrf)])
async def toggle(request: Request, slug: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if slug not in BY_SLUG:
        return RedirectResponse("/automacoes", status_code=303)
    form = await request.form()
    rows = ensure_rows(db)
    row = rows[slug]
    before = {"ativo": row.enabled}
    row.enabled = form.get("enabled") == "1"
    if row.enabled:
        row.enabled_at = utcnow()  # só avisa o que acontecer daqui para frente
        row.last_error = None
    audit.record(db, actor=f"user:{user.id}", event="automacao.ligada" if row.enabled else "automacao.desligada",
                 target_type="automacao", target_id=slug, details=audit.changes(before, {"ativo": row.enabled}))
    db.commit()
    flash(request, f"“{BY_SLUG[slug].name}” {'ligada: avisa o que acontecer daqui para frente' if row.enabled else 'desligada'}.")
    return RedirectResponse("/automacoes", status_code=303)


@router.post("/{slug}/config", dependencies=[Depends(verify_csrf)])
async def configure(request: Request, slug: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if slug not in BY_SLUG:
        return RedirectResponse("/automacoes", status_code=303)
    form = await request.form()
    row = ensure_rows(db)[slug]
    before = dict(row.params or {})
    row.params = clean_params(BY_SLUG[slug], dict(form))
    audit.record(db, actor=f"user:{user.id}", event="automacao.regras_alteradas", target_type="automacao",
                 target_id=slug, details=audit.changes(before, row.params))
    db.commit()
    flash(request, "Regra salva.")
    return RedirectResponse("/automacoes", status_code=303)


@router.post("/geral", dependencies=[Depends(verify_csrf)])
async def general(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    form = await request.form()
    emails = parse_recipients(str(form.get("recipients", "")))
    bad = invalid_recipients(emails)
    if bad:
        flash(request, f"E-mail inválido: {', '.join(bad)}", "erro")
        return RedirectResponse("/automacoes", status_code=303)

    def hour(name: str, default: int) -> int:
        try:
            return max(0, min(23, int(str(form.get(name, default)))))
        except ValueError:
            return default

    before = {"silencio": quiet_settings(db), "destinatarios": get_setting(db, RECIPIENTS_KEY) or ""}
    quiet = {"ativo": form.get("quiet_on") == "1", "de": hour("quiet_from", 22), "ate": hour("quiet_to", 7)}
    set_setting(db, QUIET_KEY, quiet)
    set_setting(db, RECIPIENTS_KEY, ", ".join(emails))
    audit.record(db, actor=f"user:{user.id}", event="automacao.geral_alterada", target_type="automacao",
                 target_id="geral", details=audit.changes(before, {"silencio": quiet, "destinatarios": ", ".join(emails)}))
    db.commit()
    flash(request, "Configurações dos avisos salvas.")
    return RedirectResponse("/automacoes", status_code=303)


@router.post("/teste", dependencies=[Depends(verify_csrf)])
def send_test(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Manda um e-mail de exemplo, para conferir se os avisos chegam (não marca nada como avisado)."""
    to = recipients(db)
    sample = Alert(source="estoque", severity="critico", title="Exemplo: Pastilha de freio CG 160 esgotou",
                   detail="Este é só um teste da tela Automações.")
    subject, text, html = build_email({"estoque": [sample]})
    try:
        if not to:
            raise EmailError("Cadastre quem recebe os avisos antes de testar.")
        send_email(to, "[Teste] " + subject, text, html)
    except EmailError as exc:
        flash(request, str(exc), "erro")
        return RedirectResponse("/automacoes", status_code=303)
    audit.record(db, actor=f"user:{user.id}", event="automacao.teste", details={"destinatarios": len(to)})
    db.commit()
    flash(request, f"E-mail de teste enviado para {', '.join(to)}.")
    return RedirectResponse("/automacoes", status_code=303)
