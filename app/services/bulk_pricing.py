"""Precificação em massa (Mercado Livre): prévia e propostas — nada muda sem aprovação.

Mesma conta de lucro do agente de Preços (app/agents/prices.py):
    lucro(p) = p − taxa(p) − frete − custo do produto (Onix; sem ele, Olist) − imposto(p)
A taxa vem da referência oficial de preço do ML de cada anúncio. As mudanças
viram Ações "mudar_preco" no ActionGateway: aprovar (uma a uma ou em lote) é que
envia ao Mercado Livre — e no modo simulação nada é enviado.

Ficam de fora (como no agente): automatização de preços do ML ligada, anúncio
com variações, anúncio em promoção e produto sem custo.
"""

import math
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.config import agent_rules
from app.core import actions
from app.models import Action, Agent, PriceReference, Product
from app.models.enums import ActionStatus, ActionType, Channel
from app.services.marketplace_fees import load_fees
from app.services.pricing_calc import round_up_price
from app.services.search import matches, terms
from app.services.store_settings import get_setting
from app.services.costs import unit_cost

RULES = {"margem": "Preço para uma margem", "reajuste": "Reajuste em %", "referencia": "Ir para a referência do ML"}
MAX_ROWS = 300
MAX_DECIDE = 100  # aprovações por vez (cada uma é uma chamada ao Mercado Livre)
BIG_CHANGE_PCT = 30.0


@dataclass
class Rule:
    kind: str = "margem"
    value: float = 20.0  # margem desejada (%) ou reajuste (%)
    ending_90: bool = True
    q: str = ""


@dataclass
class PreviewRow:
    ref: PriceReference
    sku: str | None
    cost_cents: int | None
    new_cents: int | None
    profit_now: int | None
    profit_new: int | None
    blocked: str | None  # motivo de não poder mudar
    warnings: list[str]
    pending: bool

    @property
    def margin_now(self) -> float | None:
        return self.profit_now / self.ref.current_cents * 100 if self.profit_now is not None and self.ref.current_cents else None

    @property
    def margin_new(self) -> float | None:
        return self.profit_new / self.new_cents * 100 if self.profit_new is not None and self.new_cents else None

    @property
    def change_pct(self) -> float | None:
        if not self.new_cents or not self.ref.current_cents:
            return None
        return (self.new_cents - self.ref.current_cents) / self.ref.current_cents * 100

    @property
    def selectable(self) -> bool:
        return not self.blocked and self.new_cents is not None and self.new_cents != self.ref.current_cents

    @property
    def preselected(self) -> bool:
        return self.selectable and not self.warnings


def _fee_rate(ref: PriceReference, fallback_pct: float) -> float:
    if ref.suggested_cents and ref.selling_fee_cents:
        return ref.selling_fee_cents / ref.suggested_cents
    return fallback_pct / 100


def _profit(ref: PriceReference, price: int, cost: int, tax_pct: float, rate: float) -> int:
    return round(price - price * rate - ref.shipping_fee_cents - cost - price * tax_pct / 100)


def min_margin(db: Session) -> float:
    agent = db.scalar(select(Agent).where(Agent.slug == "precos"))
    return float(agent_rules(agent)["margem_minima_pct"]) if agent else 15.0


def _target_price(rule: Rule, ref: PriceReference, cost: int, tax_pct: float, rate: float) -> int | None:
    if rule.kind == "margem":
        room = 1 - rate - tax_pct / 100 - rule.value / 100
        if room <= 0:
            return None
        price = math.ceil((ref.shipping_fee_cents + cost) / room)
    elif rule.kind == "reajuste":
        price = round(ref.listed_cents * (1 + rule.value / 100))
    else:
        price = ref.suggested_cents or None
    if price is None or price <= 0:
        return None
    return round_up_price(price, True) if rule.ending_90 else price


def preview(db: Session, rule: Rule) -> tuple[list[PreviewRow], float]:
    tax_pct = float(get_setting(db, "tax_rate_pct") or 0)
    fallback = float(load_fees(db)["ml_classico"]["fallback_pct"])
    floor_margin = min_margin(db)
    stmt = select(PriceReference).where(PriceReference.channel == Channel.ML).order_by(PriceReference.title)
    products = {p.id: p for p in db.scalars(select(Product))}
    pending = {a.target_id for a in db.scalars(select(Action).where(
        Action.type == ActionType.UPDATE_PRICE, Action.status.in_([ActionStatus.PENDING, ActionStatus.FAILED])))}
    wanted = terms(rule.q)
    refs = [ref for ref in db.scalars(stmt) if matches(
        wanted, ref.title, ref.item_id, products[ref.product_id].sku if ref.product_id in products else None)]
    rows = []
    for ref in refs[:MAX_ROWS]:
        product = products.get(ref.product_id) if ref.product_id else None
        cost = (unit_cost(product, db) or None) if product else None
        blocked = None
        if ref.automated:
            blocked = "Automatização de preços do ML ligada"
        elif ref.has_variations:
            blocked = "Anúncio com variações"
        elif ref.promoted:
            blocked = "Em promoção (ajuste no ML)"
        elif not cost:
            blocked = "Sem custo (nem no Onix nem no Olist)"
        rate = _fee_rate(ref, fallback)
        new = profit_new = None
        warnings: list[str] = []
        profit_now = _profit(ref, ref.current_cents, cost, tax_pct, rate) if cost else None
        if not blocked:
            new = _target_price(rule, ref, cost, tax_pct, rate)
            if new is None:
                blocked = "Margem impossível com as taxas deste anúncio" if rule.kind == "margem" else \
                    "Sem referência de preço do ML"
            else:
                profit_new = _profit(ref, new, cost, tax_pct, rate)
                if profit_new < 0:
                    warnings.append("dá prejuízo")
                elif profit_new / new * 100 < floor_margin:
                    warnings.append(f"abaixo da margem mínima ({floor_margin:.0f}%)")
                change = abs(new - ref.current_cents) / ref.current_cents * 100 if ref.current_cents else 0
                if change > BIG_CHANGE_PCT:
                    warnings.append(f"muda mais de {BIG_CHANGE_PCT:.0f}%")
        rows.append(PreviewRow(ref, product.sku if product else None, cost, new, profit_now, profit_new, blocked,
                               warnings, str(ref.id) in pending))
    return rows, floor_margin


def describe(rule: Rule) -> str:
    if rule.kind == "margem":
        text = f"preço para {rule.value:g}% de margem"
    elif rule.kind == "reajuste":
        text = f"reajuste de {rule.value:+g}%"
    else:
        text = "preço de referência do Mercado Livre"
    return text + (" (final ,90)" if rule.ending_90 else "")


def propose_selected(db: Session, rule: Rule, ref_ids: set[int], *, actor: str) -> int:
    """Cria as propostas para os anúncios marcados (refazendo a conta no servidor)."""
    rows, _floor = preview(db, rule)
    created = 0
    for row in rows:
        if row.ref.id not in ref_ids or not row.selectable:
            continue
        for old in db.scalars(select(Action).where(
                Action.type == ActionType.UPDATE_PRICE, Action.target_id == str(row.ref.id),
                Action.status.in_([ActionStatus.PENDING, ActionStatus.FAILED]))):
            actions.reject(db, old, actor=actor, reason="Substituída pela precificação em massa")
        actions.propose(db, agent_slug=None, type=ActionType.UPDATE_PRICE, channel=row.ref.channel,
                        target_type="anuncio", target_id=row.ref.id,
                        payload={"price_cents": row.new_cents, "old_cents": row.ref.current_cents,
                                 "item_id": row.ref.item_id},
                        topic="massa", reason=f"Precificação em massa: {describe(rule)}.")
        created += 1
    return created


def pending_prices(db: Session) -> list[tuple[Action, PriceReference | None]]:
    rows = list(db.scalars(select(Action).where(Action.type == ActionType.UPDATE_PRICE,
                                                Action.status.in_([ActionStatus.PENDING, ActionStatus.FAILED]))
                           .order_by(Action.created_at.desc())))
    refs = {r.id: r for r in db.scalars(select(PriceReference).where(
        PriceReference.id.in_([int(a.target_id) for a in rows if a.target_id.isdigit()])))}
    return [(a, refs.get(int(a.target_id)) if a.target_id.isdigit() else None) for a in rows]
