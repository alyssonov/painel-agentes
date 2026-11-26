"""Precificação: calculadora "Saia do Prejuízo" (criada pelo dono) e tabela de taxas.

A calculadora roda no navegador (app/web/static/js/calculadora.js): só faz
contas, nada é salvo nem alterado em lugar nenhum. Mudar preço de anúncio
continua sendo pelo agente de Preços ou por Preços em massa, sempre com
aprovação. A tabela de taxas da Shopee/TikTok é editável em /precificacao/taxas.
"""

import copy

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core import audit
from app.core.db import get_db
from app.models.user import User
from app.services.marketplace_fees import CHECKED, ORDER, SOURCES, load_fees, save_fees
from app.services.pricing_calc import parse_money, parse_percent
from app.services.store_settings import get_setting
from app.web.auth import current_user, verify_csrf
from app.web.templating import flash, render

router = APIRouter(prefix="/precificacao")

# Valores iniciais da calculadora (os mesmos da versão original do dono).
DEFAULTS = {"custo": "45,00", "tarifa": "16", "fixa": "6,00", "ads": "0", "imposto": "6", "outros": "3,50",
            "margem": "20", "markup": "40", "atual": "89,90", "desconto": "0"}


def _fmt_money(cents: int | None) -> str:
    return "" if cents is None else f"{cents / 100:.2f}".replace(".", ",")


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return ""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


@router.get("")
def calculator(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    values = dict(DEFAULTS)
    # Imposto: começa com a alíquota da loja (Contas conectadas), se já foi informada.
    tax = float(get_setting(db, "tax_rate_pct") or 0)
    if tax > 0:
        values["imposto"] = _fmt_pct(tax)
    return render(request, "pricing.html", {"v": values}, db=db)


@router.get("/taxas")
def fees_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return render(request, "pricing_fees.html", {"fees": load_fees(db), "order": ORDER, "sources": SOURCES,
                                                 "checked": CHECKED, "fmt_money": _fmt_money, "fmt_pct": _fmt_pct},
                  db=db)


@router.post("/taxas", dependencies=[Depends(verify_csrf)])
async def save_fees_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Salva a tabela de taxas (Shopee/TikTok por faixa; % de reserva do Mercado Livre)."""
    form = await request.form()
    fees = load_fees(db)
    before = copy.deepcopy(fees)
    errors = []
    for key in ("shopee", "tiktok"):
        bands = []
        for i in range(6):
            pct = parse_percent(form.get(f"{key}_pct{i}"))
            if pct is None:
                continue
            max_cents = parse_money(form.get(f"{key}_max{i}"))
            bands.append({"max": max_cents, "pct": pct, "fixed": parse_money(form.get(f"{key}_fixed{i}")) or 0})
        if not bands or sum(1 for b in bands if b["max"] is None) != 1:
            errors.append(f"{fees[key]['name']}: preencha as faixas e deixe só a última sem valor máximo.")
            continue
        fees[key]["bands"] = bands
        fees[key]["return_fee"] = parse_money(form.get(f"{key}_return")) or 0
    for key in ("ml_classico", "ml_premium"):
        pct = parse_percent(form.get(f"{key}_fallback"))
        if pct is not None:
            fees[key]["fallback_pct"] = pct
    if errors:
        for e in errors:
            flash(request, e, "erro")
        return RedirectResponse("/precificacao/taxas", status_code=303)
    save_fees(db, fees)
    audit.record(db, actor=f"user:{user.id}", event="precificacao.taxas_alteradas",
                 details=audit.changes(before, fees))
    db.commit()
    flash(request, "Tabela de taxas salva.")
    return RedirectResponse("/precificacao/taxas", status_code=303)
