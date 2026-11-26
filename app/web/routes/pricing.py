"""Precificação: calculadora de preço por marketplace (margem → preço e preço → margem).

Só faz contas (GET, nada é alterado em lugar nenhum). Mudar preço de anúncio
continua sendo pelo agente de Preços, com aprovação.
"""

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import PriceReference, Product
from app.models.user import User
from app.services.pricing_calc import (
    Channel,
    Costs,
    break_even,
    evaluate,
    parse_money,
    parse_percent,
    price_for,
)
from app.services.store_settings import get_setting
from app.web.auth import current_user
from app.web.templating import render

router = APIRouter(prefix="/precificacao")

MODES = [("margem", "Quero uma margem de"), ("lucro", "Quero um lucro por venda de"), ("preco", "Vou vender a")]
DEFAULT_NAMES = ["Mercado Livre Clássico", "Mercado Livre Premium", "Shopee"]
COLUMNS = 3


@dataclass
class Column:
    name: str
    commission: str
    fixed_fee: str
    shipping: str
    source: str | None = None  # de onde veio o valor pré-preenchido


def _fmt_money(cents: int | None) -> str:
    return "" if cents is None else f"{cents / 100:.2f}".replace(".", ",")


def _fmt_pct(value: float | None) -> str:
    return "" if value is None else (f"{value:.2f}".rstrip("0").rstrip(",.").replace(".", ","))


def _product_matches(db: Session, q: str) -> list[Product]:
    like = f"%{q.strip()}%"
    return list(db.scalars(select(Product).where(
        Product.is_active.is_(True), or_(Product.sku.ilike(like), Product.name.ilike(like)))
        .order_by(Product.name).limit(12)))


def _ml_reference(db: Session, product: Product) -> PriceReference | None:
    """Taxa e frete que o próprio Mercado Livre informa para um anúncio deste produto."""
    return db.scalar(select(PriceReference).where(PriceReference.product_id == product.id,
                                                  PriceReference.suggested_cents > 0)
                     .order_by(PriceReference.checked_at.desc()))


@router.get("")
def calculator(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = request.query_params
    product = db.get(Product, int(q["produto"])) if q.get("produto", "").isdigit() else None
    matches = _product_matches(db, q["busca"]) if q.get("busca") and not product else []

    columns = [Column(q.get(f"nome{i}") or DEFAULT_NAMES[i], q.get(f"comissao{i}", ""), q.get(f"taxa{i}", ""),
                      q.get(f"frete{i}", "")) for i in range(COLUMNS)]
    cost_text = q.get("custo", "")
    if product is not None and not q.get("calcular"):
        # Produto recém-escolhido: custo do Olist e, se houver, taxas do anúncio no Mercado Livre.
        cost_text = _fmt_money(product.cost_cents) if product.cost_cents else ""
        ref = _ml_reference(db, product)
        if ref is not None:
            rate = ref.selling_fee_cents / ref.suggested_cents * 100
            columns[0] = Column("Mercado Livre (seu anúncio)", _fmt_pct(round(rate, 2)), "",
                                _fmt_money(ref.shipping_fee_cents),
                                f"Taxa e frete informados pelo Mercado Livre para o anúncio {ref.item_id}")
    tax_text = q.get("imposto") if "imposto" in q else _fmt_pct(float(get_setting(db, "tax_rate_pct") or 0))
    mode = q.get("modo") if q.get("modo") in dict(MODES) else "margem"
    target_text = q.get("alvo", "20" if mode == "margem" else "")
    ending_90 = q.get("final90") == "1"

    errors, results = [], []
    if q.get("calcular"):
        cost = parse_money(cost_text)
        costs = Costs(cost_cents=cost or 0, tax_pct=parse_percent(tax_text) or 0,
                      ads_pct=parse_percent(q.get("publicidade")) or 0, other_pct=parse_percent(q.get("outros_pct")) or 0,
                      other_cents=parse_money(q.get("outros_valor")) or 0)
        target_pct = parse_percent(target_text) if mode == "margem" else None
        target_money = parse_money(target_text) if mode in ("lucro", "preco") else None
        if cost is None:
            errors.append("Informe o custo do produto.")
        if mode == "margem" and target_pct is None:
            errors.append("Informe a margem desejada (ex.: 20).")
        if mode in ("lucro", "preco") and target_money is None:
            errors.append("Informe o valor em reais (ex.: 15,00).")
        for col in columns:
            commission = parse_percent(col.commission)
            if commission is None:
                continue  # coluna vazia: não entra na comparação
            channel = Channel(col.name, commission, parse_money(col.fixed_fee) or 0, parse_money(col.shipping) or 0)
            if errors:
                continue
            if mode == "preco":
                result = evaluate(costs, channel, target_money)
            elif mode == "lucro":
                result = price_for(costs, channel, profit_cents=target_money, ending_90=ending_90)
            else:
                result = price_for(costs, channel, margin_pct=target_pct, ending_90=ending_90)
            results.append({"col": col, "r": result, "floor": break_even(costs, channel)})
        if not errors and not results:
            errors.append("Preencha a comissão de pelo menos um marketplace.")
    valid = [x for x in results if x["r"] is not None]
    best = max(valid, key=lambda x: x["r"].profit_cents)["col"].name if len(valid) > 1 else None
    return render(request, "pricing.html", {
        "product": product, "matches": matches, "busca": q.get("busca", ""), "columns": columns,
        "cost_text": cost_text, "tax_text": tax_text, "ads_text": q.get("publicidade", ""),
        "other_pct_text": q.get("outros_pct", ""), "other_cents_text": q.get("outros_valor", ""),
        "mode": mode, "modes": MODES, "target_text": target_text, "ending_90": ending_90,
        "errors": errors, "results": results, "best": best, "calculated": bool(q.get("calcular")),
    }, db=db)
