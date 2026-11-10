"""Agente 4 — Preços (Mercado Livre).

Usa as REFERÊNCIAS DE PREÇO oficiais do Mercado Livre (comparação com anúncios
parecidos) e o custo do Olist para decidir, anúncio por anúncio:

  acima da concorrência → propõe baixar para o preço de referência, SE o lucro
      nesse preço continuar acima da margem mínima; senão, propõe o menor preço
      que mantém a margem (se ainda for abaixo do atual) ou só avisa que a
      concorrência vende abaixo do que dá lucro para a loja;
  abaixo da concorrência → propõe subir até o preço de referência (mais lucro).

Lucro num preço p (custos do ML para o preço sugerido, confirmados com dados reais):
    p − taxa(p) − frete − custo do Olist − imposto(p),  taxa(p) = p × (taxa sugerida ÷ preço sugerido)

Nunca muda preço sozinho no modo manual: cria uma Ação "mudar_preco" no
ActionGateway. Fica de fora: automatização de preços do ML ligada, anúncio com
variações e anúncio em promoção (mudar o preço cadastrado pode derrubar a promoção).
Sem IA (custo zero).
"""

import math
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.base import run_agent
from app.agents.config import agent_rules, has_real_data
from app.core import actions
from app.core.money import format_brl
from app.models import Action, PriceReference, Product
from app.models.enums import ActionStatus, ActionType, Channel
from app.services.store_settings import get_setting

SLUG = "precos"
ABOVE = {"with_benchmark_high", "with_benchmark_highest"}
BELOW = {"no_benchmark_lowest"}

VERDICT_LABELS = {
    "baixar": "Baixar para a referência",
    "baixar_parcial": "Baixar até a margem mínima",
    "subir": "Dá para subir",
    "abaixo_do_custo": "Concorrência abaixo do seu custo",
    "nao_compensa": "Baixar não compensa",
    "ok": "Preço no ponto",
    "promocao": "Em promoção",
    "automatico": "Automatização do ML",
    "variacoes": "Anúncio com variações",
    "sem_custo": "Sem custo no Olist",
    "aplicado": "Preço alterado",
    "simulado": "Aprovado (simulação)",
}


@dataclass
class Analysis:
    verdict: str
    note: str
    proposed_cents: int | None = None
    profit_at_suggested: int | None = None
    profit_at_proposed: int | None = None


def profit_at(ref: PriceReference, price_cents: int, cost_cents: int, tax_pct: float) -> int:
    """Lucro estimado vendendo a `price_cents` (centavos)."""
    rate = ref.selling_fee_cents / ref.suggested_cents if ref.suggested_cents else 0.0
    return round(price_cents - price_cents * rate - ref.shipping_fee_cents - cost_cents - price_cents * tax_pct / 100)


def min_price_for_margin(ref: PriceReference, cost_cents: int, tax_pct: float, margin_pct: float) -> int | None:
    """Menor preço que ainda dá a margem mínima (lucro ÷ preço)."""
    rate = ref.selling_fee_cents / ref.suggested_cents if ref.suggested_cents else 0.0
    room = 1 - rate - tax_pct / 100 - margin_pct / 100
    if room <= 0:
        return None
    return math.ceil((ref.shipping_fee_cents + cost_cents) / room)


def analyze(ref: PriceReference, cost_cents: int | None, tax_pct: float, min_margin_pct: float) -> Analysis:
    if ref.automated:
        return Analysis("automatico", "A automatização de preços do Mercado Livre está ligada neste anúncio.")
    if ref.has_variations:
        return Analysis("variacoes", "Anúncio com variações: o preço é por variação; ajuste no Mercado Livre.")
    if not cost_cents:
        return Analysis("sem_custo", "Sem custo do produto no Olist: não dá para calcular o lucro.")
    sug = ref.suggested_cents
    profit_sug = profit_at(ref, sug, cost_cents, tax_pct) if sug else None
    margin_sug = profit_sug / sug * 100 if sug and profit_sug is not None else None
    if ref.promoted:
        return Analysis("promocao", f"Vende com promoção a {format_brl(ref.current_cents)} (cadastrado "
                        f"{format_brl(ref.listed_cents)}). Mudar o preço pode derrubar a promoção: ajuste no ML.",
                        profit_at_suggested=profit_sug)
    if ref.status in ABOVE and sug and sug < ref.current_cents:
        if ref.applicable and margin_sug is not None and margin_sug >= min_margin_pct:
            return Analysis("baixar", f"A concorrência vende a {format_brl(sug)}. Nesse preço o lucro fica em "
                            f"{format_brl(profit_sug)} ({margin_sug:.0f}%).", sug, profit_sug, profit_sug)
        floor = min_price_for_margin(ref, cost_cents, tax_pct, min_margin_pct)
        if floor is not None and sug < floor < ref.current_cents - 50:
            profit_floor = profit_at(ref, floor, cost_cents, tax_pct)
            return Analysis("baixar_parcial", f"A concorrência vende a {format_brl(sug)}, o que daria lucro de "
                            f"{format_brl(profit_sug)}. {format_brl(floor)} é o menor preço que mantém "
                            f"{min_margin_pct:.0f}% de margem.", floor, profit_sug, profit_floor)
        if profit_sug is not None and profit_sug < 0:
            return Analysis("abaixo_do_custo", f"A concorrência vende a {format_brl(sug)}: nesse preço você teria "
                            f"prejuízo de {format_brl(-profit_sug)} por venda.", profit_at_suggested=profit_sug)
        return Analysis("nao_compensa", f"A concorrência vende a {format_brl(sug)}: o lucro cairia para "
                        f"{format_brl(profit_sug)} ({margin_sug:.0f}%), abaixo da margem mínima de "
                        f"{min_margin_pct:.0f}%.", profit_at_suggested=profit_sug)
    if ref.status in BELOW and sug and sug > ref.current_cents:
        gain = profit_sug - profit_at(ref, ref.current_cents, cost_cents, tax_pct)
        return Analysis("subir", f"Você está abaixo da concorrência ({format_brl(sug)}). Subindo, ganha cerca de "
                        f"{format_brl(gain)} a mais por venda.", sug, profit_sug, profit_sug)
    return Analysis("ok", "Preço alinhado com a concorrência.", profit_at_suggested=profit_sug)


def _pending(db: Session, ref: PriceReference) -> Action | None:
    return db.scalar(select(Action).where(Action.type == ActionType.UPDATE_PRICE, Action.target_id == str(ref.id),
                                          Action.status.in_([ActionStatus.PENDING, ActionStatus.FAILED])))


def run_prices_agent(session_factory: sessionmaker[Session]):
    """Uma rodada: analisa as referências lidas e propõe mudanças de preço (nada muda sem aprovação)."""
    with session_factory() as db:
        if not has_real_data(db):
            return None

        def work(db: Session, agent) -> tuple[int, str]:
            rules = agent_rules(agent)
            min_margin, max_change = rules["margem_minima_pct"], rules["variacao_maxima_pct"]
            tax = float(get_setting(db, "tax_rate_pct") or 0)
            proposed = 0
            for ref in db.scalars(select(PriceReference).where(PriceReference.channel == Channel.ML,
                                                               PriceReference.is_demo.is_(False))):
                product = db.get(Product, ref.product_id) if ref.product_id else None
                result = analyze(ref, product.cost_cents if product else None, tax, min_margin)
                ref.verdict, ref.note, ref.profit_at_suggested_cents = result.verdict, result.note, \
                    result.profit_at_suggested
                old = _pending(db, ref)
                if result.proposed_cents is None:
                    if old is not None:
                        actions.reject(db, old, actor="system", reason="A referência de preço mudou")
                    continue
                if old is not None:
                    if old.payload.get("price_cents") == result.proposed_cents:
                        continue
                    actions.reject(db, old, actor="system", reason="Nova referência de preço")
                change = abs(result.proposed_cents - ref.current_cents) / ref.current_cents * 100
                actions.propose(
                    db, agent_slug=SLUG, type=ActionType.UPDATE_PRICE, channel=ref.channel, target_type="anuncio",
                    target_id=ref.id, payload={"price_cents": result.proposed_cents, "old_cents": ref.current_cents,
                                               "item_id": ref.item_id},
                    confidence="alta" if ref.compared >= 3 else "media", topic=result.verdict, reason=result.note,
                    auto_ok=change <= max_change)
                proposed += 1
            db.commit()
            waiting = db.scalar(select(Action.id).where(Action.type == ActionType.UPDATE_PRICE,
                                                        Action.status == ActionStatus.PENDING).limit(1))
            below = sum(1 for r in db.scalars(select(PriceReference).where(PriceReference.verdict == "abaixo_do_custo")))
            parts = [f"{proposed} sugestão(ões) nova(s)" if proposed else "Nenhuma sugestão nova"]
            if waiting:
                parts.append("há preços aguardando você")
            if below:
                parts.append(f"{below} anúncio(s) com concorrência abaixo do custo")
            return proposed, " · ".join(parts)

        return run_agent(db, SLUG, work)
