"""Visão geral do painel."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.user import User
from app.services import dashboard
from app.web.auth import current_user
from app.web.templating import render

router = APIRouter()

CHANNEL_FILTERS = [("", "Todos"), ("ml", "Mercado Livre"), ("shopee", "Shopee")]


@router.get("/")
def overview(request: Request, db: Session = Depends(get_db), _user: User = Depends(current_user)):
    return render(request, "overview.html", {
        "ov": dashboard.overview(db),
        "columns": dashboard.kanban(db),
        "channel": "",
        "channel_filters": CHANNEL_FILTERS,
    }, db=db)


@router.get("/parciais/pedidos")
def orders_partial(request: Request, canal: str = "", db: Session = Depends(get_db),
                   _user: User = Depends(current_user)):
    """Só o quadro de pedidos — usado pelo filtro Todos / Mercado Livre / Shopee."""
    return render(request, "partials/kanban.html", {
        "columns": dashboard.kanban(db, canal or None),
        "channel": canal,
        "channel_filters": CHANNEL_FILTERS,
    })


@router.get("/parciais/agentes")
def agents_partial(request: Request, db: Session = Depends(get_db), _user: User = Depends(current_user)):
    """Lista 'Agentes agora' — atualizada sozinha a cada minuto."""
    return render(request, "partials/agents.html", {"ov": dashboard.overview(db)})
