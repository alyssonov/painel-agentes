"""Seções do menu que ganham conteúdo nas próximas fases."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.user import User
from app.web.auth import current_user
from app.web.templating import render

router = APIRouter()

# endereço -> (título, fase em que chega, o que vai ter)
SECTIONS = {
    "/anuncios": ("Anúncios", 7, "Criação de anúncios e sugestões de melhoria para os anúncios fracos."),
    "/estoque-precos": ("Estoque e preços", 4, "Previsão de fim de estoque, divergências e sugestões de preço."),
    "/pos-venda": ("Pós-venda", 6, "Mensagens dos pedidos e reclamações abertas."),
    "/margem": ("Margem real", 4, "Lucro e margem de cada venda e de cada produto, com destaque para os prejuízos."),
    "/relatorios": ("Relatórios", 5, "Relatório diário por WhatsApp/e-mail e relatórios semanal e mensal em PDF."),
}


def _make_view(path: str):
    title, phase, what = SECTIONS[path]

    def view(request: Request, db: Session = Depends(get_db), _user: User = Depends(current_user)):
        return render(request, "coming_soon.html", {"title": title, "phase": phase, "what": what}, db=db)

    return view


for _path in SECTIONS:
    router.add_api_route(_path, _make_view(_path), methods=["GET"])
