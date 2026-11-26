"""Precificação: calculadora de preço por marketplace (formato inspirado na "calculadora fácil" da NorteUp).

Só faz contas (nada é alterado em lugar nenhum). Mudar preço de anúncio continua
sendo pelo agente de Preços, com aprovação. A tabela de taxas da Shopee/TikTok
é editável em /precificacao/taxas.
"""

import copy
from dataclasses import dataclass, field
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.db import get_db, get_sessionmaker, utcnow
from app.integrations.mercadolivre.auth import MLTokenProvider, get_ml_account
from app.integrations.mercadolivre.client import MLClient
from app.models import Listing, PriceReference, PricingSimulation, Product
from app.models.user import User
from app.services.marketplace_fees import (
    CHECKED,
    ORDER,
    SOURCES,
    load_fees,
    ml_fee,
    save_fees,
    store_logistic_type,
)
from app.services.pricing_calc import (
    Channel,
    Costs,
    band_fee,
    break_even,
    evaluate_listed,
    parse_money,
    parse_percent,
    price_for,
)
from app.services.store_settings import get_setting
from app.web.auth import current_user, verify_csrf
from app.web.templating import flash, render

router = APIRouter(prefix="/precificacao")

MARGIN_CHIPS = ["10", "20", "30"]
ADS_CHIPS = [("0", "Não"), ("5", "5%"), ("10", "10%")]
HISTORY_SIZE = 10


def _fmt_money(cents: int | None) -> str:
    return "" if cents is None else f"{cents / 100:.2f}".replace(".", ",")


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return ""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


@dataclass
class CardView:
    key: str
    name: str
    result: object | None
    floor: int | None
    source: str
    simulated: object | None = None
    sim_text: str = ""
    notes: list[str] = field(default_factory=list)


def _product_matches(db: Session, q: str) -> list[Product]:
    like = f"%{q.strip()}%"
    return list(db.scalars(select(Product).where(
        Product.is_active.is_(True), or_(Product.sku.ilike(like), Product.name.ilike(like)))
        .order_by(Product.name).limit(12)))


def _ml_reference(db: Session, product: Product) -> PriceReference | None:
    return db.scalar(select(PriceReference).where(PriceReference.product_id == product.id,
                                                  PriceReference.suggested_cents > 0)
                     .order_by(PriceReference.checked_at.desc()))


def _ml_client_factory(db: Session):
    account = get_ml_account(db)
    if account is None or not account.access_token_enc or account.is_demo:
        return None
    return lambda: MLClient(MLTokenProvider(get_sessionmaker()))


def _category_for(product: Product | None, db: Session, factory) -> str | None:
    """Categoria do anúncio do produto no ML (a tarifa depende dela)."""
    if product is None or factory is None:
        return None
    listing = db.scalar(select(Listing).where(Listing.product_id == product.id, Listing.channel == "ml"))
    if listing is None:
        return None
    try:
        with factory() as client:
            return (client.get_item(listing.item_id) or {}).get("category_id")
    except Exception:  # noqa: BLE001 - sem categoria, a API dá a tarifa geral
        return None


@router.get("")
def calculator(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = request.query_params
    product = db.get(Product, int(q["produto"])) if q.get("produto", "").isdigit() else None
    matches = _product_matches(db, q["busca"]) if q.get("busca") and not product else []
    fees = load_fees(db)
    calculating = bool(q.get("calcular"))

    # Valores do formulário (produto recém-escolhido preenche custo e frete do ML).
    ref = _ml_reference(db, product) if product else None
    name = q.get("nome") or (product.name if product else "")
    cost_text = q.get("custo") if calculating else (_fmt_money(product.cost_cents) if product and product.cost_cents else q.get("custo", ""))
    ship_ml_text = q.get("frete_ml") if calculating else (_fmt_money(ref.shipping_fee_cents) if ref else q.get("frete_ml", ""))
    store_tax = float(get_setting(db, "tax_rate_pct") or 0)
    form = {
        "nome": name, "custo": cost_text or "", "rateio": q.get("rateio", "0"), "rateio_tipo": q.get("rateio_tipo", "pct"),
        "margem": q.get("margem", "20"), "modo": q.get("modo") if q.get("modo") in ("margem", "lucro") else "margem",
        "lucro": q.get("lucro", ""), "regime": q.get("regime", "simples"),
        "imposto": q.get("imposto") if "imposto" in q else _fmt_pct(store_tax),
        "ads": q.get("ads", "0"), "desconto": q.get("desconto", "0"), "afiliados": q.get("afiliados", "0"),
        "final90": q.get("final90") == "1" if calculating else True,
        "devolucao": q.get("devolucao") == "1", "frete_ml": ship_ml_text or "",
        "mk": q.getlist("mk") if calculating else ["shopee", "ml_classico", "ml_premium"],
    }

    errors, cards = [], []
    if calculating:
        cost = parse_money(form["custo"])
        rateio = parse_percent(form["rateio"]) if form["rateio_tipo"] == "pct" else None
        rateio_cents = parse_money(form["rateio"]) if form["rateio_tipo"] == "reais" else None
        costs = Costs(cost_cents=cost or 0, tax_pct=parse_percent(form["imposto"]) or 0,
                      ads_pct=parse_percent(form["ads"]) or 0, affiliate_pct=parse_percent(form["afiliados"]) or 0,
                      other_pct=rateio or 0, other_cents=rateio_cents or 0,
                      discount_pct=parse_percent(form["desconto"]) or 0)
        margin = parse_percent(form["margem"]) if form["modo"] == "margem" else None
        profit = parse_money(form["lucro"]) if form["modo"] == "lucro" else None
        if cost is None or cost == 0:
            errors.append("Informe o custo total do produto.")
        if form["modo"] == "margem" and margin is None:
            errors.append("Escolha ou digite a margem de lucro desejada.")
        if form["modo"] == "lucro" and profit is None:
            errors.append("Informe o lucro por venda desejado (ex.: 15,00).")
        if not form["mk"]:
            errors.append("Marque pelo menos um marketplace.")
        if not errors:
            factory = _ml_client_factory(db)
            category = _category_for(product, db, factory) if any(k.startswith("ml_") for k in form["mk"]) else None
            logistic = store_logistic_type(db)
            ship_ml = parse_money(form["frete_ml"]) or 0
            for key in [k for k in ORDER if k in form["mk"]]:
                spec = fees[key]
                notes = []
                if key.startswith("ml_"):
                    src = ml_fee(factory, spec["listing_type"], float(spec["fallback_pct"]),
                                 category_id=category, logistic_type=logistic)
                    channel = Channel(spec["name"], src.fee, shipping_cents=ship_ml)
                    if ship_ml:
                        notes.append(f"Inclui frete de {_fmt_money(ship_ml)} pago por você")
                else:
                    extra = int(spec.get("return_fee") or 0) if (key == "shopee" and form["devolucao"]) else 0
                    channel = Channel(spec["name"], band_fee(spec["bands"]), extra_cents=extra)
                    src = None
                    if extra:
                        notes.append(f"Inclui taxa de devolução de {_fmt_money(extra)}")
                result = price_for(costs, channel, margin_pct=margin, profit_cents=profit, ending_90=form["final90"])
                card = CardView(key, spec["name"], result, break_even(costs, channel),
                                src.label if src else f"tabela de taxas do painel (referência {CHECKED}, confira)",
                                notes=notes)
                sim = parse_money(q.get(f"sim_{key}"))
                if sim:
                    card.simulated, card.sim_text = evaluate_listed(costs, channel, sim), q.get(f"sim_{key}")
                cards.append(card)
            _save_history(db, user, name, cost, request, cards)
    valid = [c for c in cards if c.result is not None]
    best = max(valid, key=lambda c: c.result.profit_cents).name if len(valid) > 1 else None
    history = list(db.scalars(select(PricingSimulation).where(PricingSimulation.user_id == user.id)
                              .order_by(PricingSimulation.created_at.desc()).limit(HISTORY_SIZE)))
    base_query = [(k, v) for k, v in q.multi_items() if not k.startswith("sim_")]
    return render(request, "pricing.html", {
        "product": product, "matches": matches, "busca": q.get("busca", ""), "form": form, "fees": fees,
        "order": ORDER, "margin_chips": MARGIN_CHIPS, "ads_chips": ADS_CHIPS, "errors": errors, "cards": cards,
        "best": best, "calculated": calculating, "history": history, "base_query": base_query,
        "ml_connected": _ml_client_factory(db) is not None, "store_tax": _fmt_pct(store_tax),
    }, db=db)


def _save_history(db: Session, user: User, name: str, cost: int, request: Request, cards: list[CardView]) -> None:
    """Guarda a simulação (só as 10 últimas de cada pessoa)."""
    query = urlencode([(k, v) for k, v in request.query_params.multi_items()
                       if k not in ("busca",) and not k.startswith("sim_")])
    summary = [{"name": c.name, "price_cents": c.result.listed_cents, "margin_pct": c.result.margin_pct}
               for c in cards if c.result is not None]
    last = db.scalar(select(PricingSimulation).where(PricingSimulation.user_id == user.id)
                     .order_by(PricingSimulation.created_at.desc()).limit(1))
    if last is not None and last.query == query:
        return  # mesma simulação de novo (ex.: só testou um preço): não repete no histórico
    db.add(PricingSimulation(user_id=user.id, title=(name or f"Custo {_fmt_money(cost)}")[:160], query=query,
                             summary=summary, created_at=utcnow()))
    db.flush()
    old = list(db.scalars(select(PricingSimulation).where(PricingSimulation.user_id == user.id)
                          .order_by(PricingSimulation.created_at.desc()).offset(HISTORY_SIZE)))
    for row in old:
        db.delete(row)
    db.commit()


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
