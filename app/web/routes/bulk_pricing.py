"""Precificação em massa: prévia, propostas e aprovação em lote (com permissão de aprovar preços)."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core import actions, audit
from app.core.db import get_db
from app.integrations.errors import IntegrationError
from app.models import Action
from app.models.enums import ActionStatus, ActionType
from app.models.user import User
from app.services.bulk_pricing import (
    MAX_DECIDE,
    RULES,
    Rule,
    describe,
    pending_prices,
    preview,
    propose_selected,
)
from app.services.pricing_calc import parse_percent
from app.web.auth import current_user, verify_csrf
from app.web.templating import flash, render

router = APIRouter(prefix="/precificacao/massa")


def _rule(regra: str, valor: str, final90: str, busca: str) -> tuple[Rule, str | None]:
    kind = regra if regra in RULES else "margem"
    value = parse_percent(valor)
    error = None
    if kind == "margem" and (value is None or not 0 <= value <= 80):
        error, value = "Informe a margem entre 0 e 80%.", 20.0
    elif kind == "reajuste" and (value is None or not -50 <= value <= 100):
        error, value = "Informe o reajuste entre -50% e +100%.", 0.0
    return Rule(kind, value if value is not None else 0.0, final90 == "1", busca.strip()[:80]), error


@router.get("")
def bulk_page(request: Request, regra: str = "margem", valor: str = "20", final90: str = "1", busca: str = "",
              ver: str = "", db: Session = Depends(get_db), user: User = Depends(current_user)):
    rule, error = _rule(regra, valor, final90, busca)
    rows, floor = preview(db, rule) if ver else ([], None)
    return render(request, "bulk_pricing.html", {
        "rule": rule, "rules": RULES, "rows": rows, "floor": floor, "error": error, "shown": bool(ver),
        "valor": valor, "pending": pending_prices(db), "max_decide": MAX_DECIDE,
        "selectable": sum(1 for r in rows if r.selectable),
    }, db=db)


@router.post("/propor", dependencies=[Depends(verify_csrf)])
async def propose(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    form = await request.form()
    rule, error = _rule(str(form.get("regra", "")), str(form.get("valor", "")), str(form.get("final90", "")),
                        str(form.get("busca", "")))
    ids = {int(v) for v in form.getlist("ref") if str(v).isdigit()}
    if error or not ids:
        flash(request, error or "Marque pelo menos um anúncio.", "erro")
        return RedirectResponse("/precificacao/massa", status_code=303)
    created = propose_selected(db, rule, ids, actor=f"user:{user.id}")
    audit.record(db, actor=f"user:{user.id}", event="precificacao.massa_proposta",
                 details={"regra": describe(rule), "propostas": created})
    db.commit()
    flash(request, f"{created} mudança(s) de preço criada(s) para aprovação ({describe(rule)}). "
                   "Confira abaixo e aprove.")
    return RedirectResponse("/precificacao/massa#pendentes", status_code=303)


@router.post("/decidir", dependencies=[Depends(verify_csrf)])
async def decide(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Aprova ou descarta várias mudanças de preço de uma vez (cada uma passa pelo ActionGateway)."""
    form = await request.form()
    decision = str(form.get("decisao", ""))
    ids = [int(v) for v in form.getlist("acao") if str(v).isdigit()][:MAX_DECIDE]
    if decision not in ("aprovar", "descartar") or not ids:
        flash(request, "Marque as mudanças e escolha aprovar ou descartar.", "erro")
        return RedirectResponse("/precificacao/massa#pendentes", status_code=303)
    done = failed = 0
    for action in db.query(Action).filter(Action.id.in_(ids), Action.type == ActionType.UPDATE_PRICE):
        try:
            if decision == "aprovar":
                actions.approve(db, action, actor=f"user:{user.id}")
                failed += action.status == ActionStatus.FAILED
                done += action.status != ActionStatus.FAILED
            else:
                actions.reject(db, action, actor=f"user:{user.id}", reason="Descartada em lote")
                done += 1
        except (actions.ActionNotAllowed, IntegrationError):
            failed += 1
        db.commit()  # uma por vez: se o ML falhar no meio, as anteriores ficam registradas
    verb = "aprovada(s)" if decision == "aprovar" else "descartada(s)"
    msg = f"{done} mudança(s) {verb}."
    if failed:
        msg += f" {failed} não deu certo (veja o motivo na lista)."
    flash(request, msg, "erro" if failed and not done else "ok")
    return RedirectResponse("/precificacao/massa#pendentes", status_code=303)
