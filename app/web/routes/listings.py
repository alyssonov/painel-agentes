"""Seção Anúncios: qualidade dos anúncios (Mercado Livre) e sugestões de texto do agente, com aprovação."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import actions
from app.core.db import get_db
from app.models import Action, Listing, ListingQuality
from app.models.enums import ActionStatus, ActionType
from app.models.user import User
from app.services.search import matches, terms
from app.web.auth import current_user, verify_csrf
from app.web.templating import flash, render

router = APIRouter(prefix="/anuncios")

MAX_DESCRIPTION = 5000
MAX_TITLE = 60
LEVELS = {"Profissional": "ok", "Satisfatória": "atencao", "Básica": "erro"}


SEARCH_LIMIT = 200


def search_listings(db: Session, query: str) -> list[dict]:
    """Todos os anúncios (ML e Shopee) que têm as palavras buscadas, em qualquer ordem."""
    wanted = terms(query)
    if not wanted:
        return []
    quality = {q.item_id: q for q in db.scalars(select(ListingQuality))}
    found = []
    for listing in db.scalars(select(Listing).order_by(Listing.title)):
        if matches(wanted, listing.title, listing.variation_label, listing.sku, listing.item_id, listing.external_id):
            found.append({"l": listing, "q": quality.get(listing.item_id)})
            if len(found) >= SEARCH_LIMIT:
                break
    return found


@router.get("")
def listings_page(request: Request, busca: str = "", db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    busca = busca.strip()[:80]
    queue = []
    for action in db.scalars(select(Action).where(
            Action.type == ActionType.UPDATE_LISTING,
            Action.status.in_([ActionStatus.PENDING, ActionStatus.FAILED])).order_by(Action.created_at)):
        quality = db.get(ListingQuality, int(action.target_id))
        if quality is not None:
            queue.append({"a": action, "q": quality})
    rows = list(db.scalars(select(ListingQuality).where(ListingQuality.score.is_not(None))
                           .order_by(ListingQuality.score).limit(80)))
    counts = {name: 0 for name in LEVELS}
    for row in db.scalars(select(ListingQuality)):
        if row.level_wording in counts:
            counts[row.level_wording] += 1
    return render(request, "listings.html", {"queue": queue, "rows": rows, "counts": counts, "levels": LEVELS,
                                             "max_description": MAX_DESCRIPTION, "max_title": MAX_TITLE,
                                             "busca": busca, "found": search_listings(db, busca) if busca else None,
                                             "search_limit": SEARCH_LIMIT}, db=db)


@router.post("/acao/{action_id}/aprovar", dependencies=[Depends(verify_csrf)])
def approve(request: Request, action_id: int, descricao: str = Form(""), titulo: str = Form(""),
            db: Session = Depends(get_db), user: User = Depends(current_user)):
    action = db.get(Action, action_id)
    description = (descricao or "").replace("\r\n", "\n").strip()
    title = " ".join((titulo or "").split()) or None
    if action is None or action.type != ActionType.UPDATE_LISTING:
        flash(request, "Sugestão não encontrada.", "erro")
    elif not description or len(description) > MAX_DESCRIPTION:
        flash(request, f"A descrição precisa ter de 1 a {MAX_DESCRIPTION} caracteres.", "erro")
    elif title and len(title) > MAX_TITLE:
        flash(request, f"O título pode ter no máximo {MAX_TITLE} caracteres.", "erro")
    else:
        edited = {**action.payload, "description": description, "title": title if action.payload.get("title") else None}
        try:
            actions.approve(db, action, actor=f"user:{user.id}", edited_payload=edited)
            db.commit()
            if action.status == ActionStatus.SIMULATED:
                flash(request, "Aprovado. Modo simulação: nada foi alterado no Mercado Livre.")
            elif action.status == ActionStatus.EXECUTED:
                flash(request, action.result or "Anúncio atualizado.")
            else:
                flash(request, f"O anúncio não foi alterado: {action.result}", "erro")
        except actions.ActionNotAllowed as exc:
            flash(request, str(exc), "erro")
    return RedirectResponse("/anuncios", status_code=303)


@router.post("/acao/{action_id}/descartar", dependencies=[Depends(verify_csrf)])
def reject(request: Request, action_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    action = db.get(Action, action_id)
    if action is not None and action.type == ActionType.UPDATE_LISTING:
        try:
            actions.reject(db, action, actor=f"user:{user.id}", reason="Descartada no painel")
            db.commit()
            flash(request, "Sugestão descartada. O anúncio não mudou.")
        except actions.ActionNotAllowed as exc:
            flash(request, str(exc), "erro")
    return RedirectResponse("/anuncios", status_code=303)
