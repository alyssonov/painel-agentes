"""Custo do produto usado nas contas de lucro (regra do dono, 27/11/2026).

Ordem: custo do **Onix** (loja física, lido pela API do fornecedor e guardado no
cadastro complementar) e, se o Onix não tiver, o custo do **Olist**. Nada é escrito
no Olist nem no Onix: o custo do Onix só fica no banco do painel.

Para não consultar o banco produto por produto, os custos do Onix ficam num cache
curto da sessão (5 minutos).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app.core.db import utcnow
from app.models import Product
from app.models.product_extra import ProductExtra

_CACHE_KEY = "onix_costs"
_CACHE_SECONDS = 300


def onix_costs(db: Session | None) -> dict[int, int]:
    """product_id → custo do Onix (centavos), só dos produtos que têm custo no Onix."""
    if db is None:
        return {}
    now = utcnow()
    cached = db.info.get(_CACHE_KEY)
    if cached and (now - cached[0]).total_seconds() <= _CACHE_SECONDS:
        return cached[1]
    costs = {pid: cents for pid, cents in db.execute(
        select(ProductExtra.product_id, ProductExtra.onix_cost_cents)
        .where(ProductExtra.onix_cost_cents.is_not(None), ProductExtra.onix_cost_cents > 0)).all()}
    db.info[_CACHE_KEY] = (now, costs)
    return costs


def clear_cache(db: Session) -> None:
    db.info.pop(_CACHE_KEY, None)


def unit_cost(product: Product | None, db: Session | None = None) -> int:
    """Custo unitário: Onix, senão Olist; 0 quando nenhum dos dois tem."""
    if product is None:
        return 0
    onix = onix_costs(db or object_session(product)).get(product.id)
    return onix if onix else (product.cost_cents or 0)


def cost_source(product: Product | None, db: Session | None = None) -> str | None:
    """De onde veio o custo: "Onix", "Olist" ou None (sem custo)."""
    if product is None:
        return None
    if onix_costs(db or object_session(product)).get(product.id):
        return "Onix"
    return "Olist" if product.cost_cents else None
