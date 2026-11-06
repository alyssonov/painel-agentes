"""Configuração dos templates Jinja2 e dados comuns a todas as páginas."""

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.money import format_brl, format_pct
from app.core.security import get_or_create_csrf_token
from app.services.dashboard import integrations

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.filters["brl"] = format_brl
templates.env.filters["pct"] = format_pct

# Itens do menu lateral: (endereço, nome, ícone).
NAV_ITEMS = [
    ("/", "Visão geral", "home"),
    ("/perguntas", "Perguntas", "chat"),
    ("/anuncios", "Anúncios", "tag"),
    ("/estoque-precos", "Estoque e preços", "box"),
    ("/pos-venda", "Pós-venda", "truck"),
    ("/margem", "Margem real", "chart"),
    ("/relatorios", "Relatórios", "file"),
    ("/agentes", "Agentes", "bot"),
]


def flash(request: Request, message: str, kind: str = "ok") -> None:
    """Guarda uma mensagem para mostrar na próxima página (ex.: "Olist conectado!")."""
    request.session["flash"] = {"message": message, "kind": kind}


def render(request: Request, template: str, context: dict[str, Any] | None = None,
           *, db: Session | None = None, status_code: int = 200):
    """Renderiza uma página, incluindo os dados que toda página precisa."""
    accounts = integrations(db) if db is not None else []
    base = {
        "dry_run": get_settings().dry_run,
        # Há alguma conta real conectada? Muda o texto da faixa do modo simulação.
        "real_data": any(a.is_real for a in accounts),
        "csrf_token": get_or_create_csrf_token(request.session),
        "nav_items": NAV_ITEMS,
        "current_path": request.url.path,
        "user": getattr(request.state, "user", None),
        "integrations": accounts,
        "flash": request.session.pop("flash", None),
    }
    base.update(context or {})
    return templates.TemplateResponse(request, template, base, status_code=status_code)
