"""Página Relatórios: prévia do relatório de hoje, destinatários, envio de teste e histórico."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.config import agent_rules
from app.config import get_settings
from app.core import audit
from app.core.db import get_db
from app.core.timeutil import local_today, to_local
from app.models import Agent, Report
from app.models.user import User
from app.notify.email import invalid_recipients, parse_recipients
from app.reports.daily import build_daily, render_html, send_daily
from app.services.store_settings import get_setting, set_setting
from app.web.auth import current_user, verify_csrf
from app.web.templating import flash, render

router = APIRouter(prefix="/relatorios")

STATUS = {"enviado": ("Enviado", "ok"), "falhou": ("Falhou", "erro"),
          "sem_destinatario": ("Sem destinatário", "atencao"), "pendente": ("Pendente", "info")}


@router.get("")
def reports_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    agent = db.scalar(select(Agent).where(Agent.slug == "relatorio"))
    history = [{
        "r": r, "label": STATUS.get(r.status, (r.status, "info")),
        "when": to_local(r.sent_at or r.updated_at).strftime("%d/%m %H:%M"),
        "kind": "Teste" if r.kind == "teste" else "Diário",
    } for r in db.scalars(select(Report).order_by(Report.updated_at.desc()).limit(30))]
    return render(request, "reports.html", {
        "recipients": get_setting(db, "report_recipients") or "",
        "email_configured": get_settings().email_configured,
        "email_from": get_settings().email_from or get_settings().smtp_user,
        "hour": int(agent_rules(agent)["hora_envio"]) if agent else 19,
        "enabled": bool(agent and agent.enabled),
        "history": history,
    }, db=db)


@router.get("/previa", response_class=HTMLResponse)
def preview(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """O e-mail de hoje, exatamente como vai chegar (mostrado dentro da página Relatórios)."""
    response = HTMLResponse(render_html(build_daily(db, local_today())))
    # O e-mail usa estilos inline (padrão de e-mail); só esta página libera isso,
    # e só pode ser exibida dentro do próprio painel.
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'")
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response


@router.post("/destinatarios", dependencies=[Depends(verify_csrf)])
def save_recipients(request: Request, recipients: str = Form(""), db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    emails = parse_recipients(recipients)
    bad = invalid_recipients(emails)
    if bad:
        flash(request, "E-mail inválido: " + ", ".join(bad), "erro")
        return RedirectResponse("/relatorios", status_code=303)
    if len(emails) > 10:
        flash(request, "Cadastre no máximo 10 e-mails.", "erro")
        return RedirectResponse("/relatorios", status_code=303)
    set_setting(db, "report_recipients", ", ".join(emails))
    audit.record(db, actor=f"user:{user.id}", event="relatorio.destinatarios", details={"emails": len(emails)})
    db.commit()
    flash(request, f"Destinatários salvos ({len(emails)}).")
    return RedirectResponse("/relatorios", status_code=303)


@router.post("/enviar-agora", dependencies=[Depends(verify_csrf)])
def send_now(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Envio de teste: manda o resumo de hoje agora (não conta como o envio das 19h)."""
    report = send_daily(db, local_today(), triggered_by=f"user:{user.id}", test=True)
    if report.status == "enviado":
        flash(request, f"Relatório de teste enviado para {report.sent_to}.")
    else:
        flash(request, f"Não foi enviado: {report.error}", "erro")
    return RedirectResponse("/relatorios", status_code=303)
