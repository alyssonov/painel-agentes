"""Configuração dos templates Jinja2 e dados comuns a todas as páginas."""

import time
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.permissions import required_permission
from app.core.money import format_brl, format_pct
from app.core.security import get_or_create_csrf_token
from app.core.timeutil import to_local
from app.services.dashboard import integrations

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.filters["brl"] = format_brl
# Versão dos arquivos de estilo/JS: muda a cada vez que o painel liga (inclusive
# depois de uma atualização automática), então o navegador nunca usa CSS/JS antigo.
templates.env.globals["asset_v"] = str(int(time.time()))
templates.env.filters["pct"] = format_pct
# Data (UTC no banco) → dia no fuso da loja, ex.: 03/11/2026
templates.env.filters["localdate"] = lambda dt: to_local(dt).strftime("%d/%m/%Y") if dt else ""


def reputation_tone(label: str | None) -> str:
    """Cor do card de reputação: verde (boa), âmbar (amarela), vermelho (laranja/vermelha)."""
    text = (label or "").lower()
    if not text or "sem reputação" in text:
        return "info"
    if "vermelha" in text or "laranja" in text:
        return "bad"
    if "amarela" in text:
        return "warn"
    return "ok"


templates.env.filters["rep_tone"] = reputation_tone

# Menu lateral agrupado por área: (título do grupo ou None, [(endereço, nome, ícone), ...]).
# Só entram páginas que existem de verdade (nada de telas vazias "em breve").
NAV_GROUPS = [
    (None, [("/", "Visão geral", "home")]),
    ("Operação", [("/pedidos", "Pedidos", "cart"), ("/produtos", "Produtos", "box"),
                  ("/anuncios", "Anúncios", "tag"), ("/estoque-precos", "Estoque", "layers"),
                  ("/compras", "Compras", "clipboard"),
                  ("/precificacao", "Precificação", "calc")]),
    ("Atendimento", [("/perguntas", "Perguntas", "chat"), ("/pos-venda", "Pós-venda", "truck")]),
    ("Financeiro", [("/margem", "Lucro real", "coins"), ("/margem/recebimentos", "Recebimentos", "wallet"),
                    ("/financeiro/dre", "DRE do mês", "file"), ("/financeiro/despesas", "Despesas", "receipt"),
                    ("/margem/graficos", "Gráficos", "chart"), ("/margem/curva-abc", "Curva ABC", "star")]),
    ("Inteligência", [("/assistente", "Assistente", "chat"), ("/agentes", "Agentes", "bot"),
                      ("/automacoes", "Automações", "mail"), ("/relatorios", "Relatórios", "file")]),
    ("Administração", [("/equipe", "Equipe", "users"), ("/historico", "Histórico", "receipt"),
                       ("/integracoes", "Contas conectadas", "plug")]),
]
NAV_ITEMS = [item for _title, items in NAV_GROUPS for item in items]
THEMES = ("dark", "light")
# Sugestões que aparecem no chat flutuante do Assistente.
ASSISTANT_SUGGESTIONS = ["Quanto vendi hoje?", "O que está esperando por mim?", "Onde vejo o lucro de uma venda?"]
THEME_COOKIE = "tema"


def active_nav(path: str) -> str:
    """Item do menu da página atual: o endereço mais específico que combina (ex.: /margem/venda → Lucro real)."""
    best = "/"
    for href, _label, _icon in NAV_ITEMS:
        if href != "/" and (path == href or path.startswith(href + "/")) and len(href) > len(best):
            best = href
    return best if (best != "/" or path == "/") else ""


def nav_for(user) -> list:
    """Menu só com o que a pessoa pode abrir (mesmas regras do servidor)."""
    if user is None:
        return NAV_GROUPS
    groups = []
    for title, items in NAV_GROUPS:
        allowed = [item for item in items if user.can(required_permission("GET", item[0]))]
        if allowed:
            groups.append((title, allowed))
    return groups


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
        "nav_groups": nav_for(getattr(request.state, "user", None)),
        "active_nav": active_nav(request.url.path),
        "theme": request.cookies.get(THEME_COOKIE) if request.cookies.get(THEME_COOKIE) in THEMES else "dark",
        "current_path": request.url.path,
        "user": getattr(request.state, "user", None),
        # can("ver_financeiro") nos templates: esconde o que a pessoa não pode ver.
        "can": lambda perm: bool(getattr(request.state, "user", None)) and request.state.user.can(perm),
        "integrations": accounts,
        "flash": request.session.pop("flash", None),
        "assistant_suggestions": ASSISTANT_SUGGESTIONS,
    }
    base.update(context or {})
    return templates.TemplateResponse(request, template, base, status_code=status_code)
