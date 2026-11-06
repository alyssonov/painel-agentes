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


def render(request: Request, template: str, context: dict[str, Any] | None = None,
           *, db: Session | None = None, status_code: int = 200):
    """Renderiza uma página, incluindo os dados que toda página precisa."""
    base = {
        "dry_run": get_settings().dry_run,
        "csrf_token": get_or_create_csrf_token(request.session),
        "nav_items": NAV_ITEMS,
        "current_path": request.url.path,
        "user": getattr(request.state, "user", None),
        "integrations": integrations(db) if db is not None else [],
    }
    base.update(context or {})
    return templates.TemplateResponse(request, template, base, status_code=status_code)
