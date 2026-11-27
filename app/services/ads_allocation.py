"""Publicidade (Mercado Ads) dividida venda por venda, para o lucro real de cada venda.

O Mercado Livre informa quanto cada campanha gastou por dia, mas não diz quanto
custou cada venda. A divisão segue esta regra (regra do dono: o lucro de cada venda
desconta a publicidade):

1. O gasto de uma campanha num dia vai para as vendas do Mercado Livre desse mesmo
   dia que têm produto anunciado nessa campanha, na proporção do valor dos itens
   anunciados.
2. O que sobrar (campanha sem venda do produto anunciado naquele dia) é dividido entre
   todas as vendas do Mercado Livre do dia, na proporção do valor de cada uma.
3. Dia sem nenhuma venda no Mercado Livre: o gasto não cabe em venda nenhuma e aparece
   no DRE como despesa do mês ("publicidade sem venda no dia").

O resultado fica em ``orders.ads_cost_cents`` e é refeito a cada leitura da
publicidade (o número de um dia ainda muda por até 14 dias). Só leitura das APIs:
nada é alterado no Mercado Livre.
"""

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.timeutil import local_day_bounds_utc
from app.models import AdDaily, AdGroup, Listing, Order
from app.models.enums import Channel, KanbanStatus


def _split(total: int, weights: dict[int, int]) -> dict[int, int]:
    """Divide ``total`` centavos na proporção dos pesos, sem perder centavo no arredondamento."""
    weight_sum = sum(weights.values())
    if total <= 0 or weight_sum <= 0:
        return {}
    out, given = {}, 0
    keys = sorted(weights)
    for key in keys[:-1]:
        share = total * weights[key] // weight_sum
        out[key] = share
        given += share
    out[keys[-1]] = total - given
    return out


def _campaign_products(db: Session) -> dict[str, tuple[set[int], set[str]]]:
    """Campanha → (produtos, SKUs) anunciados nela, pelo anúncio (MLB…) de cada Ad Group."""
    listings: dict[str, list[Listing]] = defaultdict(list)
    for row in db.scalars(select(Listing).where(Listing.channel == Channel.ML)):
        listings[row.item_id].append(row)
    out: dict[str, tuple[set[int], set[str]]] = {}
    for group in db.scalars(select(AdGroup).where(AdGroup.campaign_id.is_not(None))):
        products, skus = out.setdefault(str(group.campaign_id), (set(), set()))
        for listing in listings.get(str(group.external_id or ""), []):
            if listing.product_id:
                products.add(listing.product_id)
            if listing.sku:
                skus.add(listing.sku)
    return out


def allocate_ads(db: Session, start: date, end: date) -> None:
    """Refaz a publicidade de cada venda do Mercado Livre entre ``start`` e ``end`` (inclusive)."""
    campaigns = _campaign_products(db)
    day = start
    while day <= end:
        _allocate_day(db, day, campaigns)
        day += timedelta(days=1)
    db.flush()


def _allocate_day(db: Session, day: date, campaigns: dict[str, tuple[set[int], set[str]]]) -> None:
    start, end = local_day_bounds_utc(day)
    orders = list(db.scalars(select(Order).where(
        Order.channel == Channel.ML, Order.is_demo.is_(False), Order.ordered_at >= start, Order.ordered_at < end)
        .options(selectinload(Order.items))))
    for order in orders:
        order.ads_cost_cents = 0
    valid = [o for o in orders if o.kanban_status != KanbanStatus.CANCELLED and o.items_total_cents > 0]
    spend = db.execute(select(AdDaily.campaign_id, AdDaily.cost_cents).where(AdDaily.day == day)).all()
    if not valid or not spend:
        return
    result: dict[int, int] = defaultdict(int)
    leftover = 0
    for campaign_id, cost in spend:
        if not cost:
            continue
        products, skus = campaigns.get(str(campaign_id), (set(), set()))
        weights = {o.id: sum(i.quantity * i.unit_price_cents for i in o.items
                             if (i.product_id and i.product_id in products) or i.sku in skus)
                   for o in valid}
        weights = {k: v for k, v in weights.items() if v > 0}
        if not weights:
            leftover += cost
            continue
        for order_id, share in _split(cost, weights).items():
            result[order_id] += share
    for order_id, share in _split(leftover, {o.id: o.items_total_cents for o in valid}).items():
        result[order_id] += share
    by_id = {o.id: o for o in valid}
    for order_id, cents in result.items():
        by_id[order_id].ads_cost_cents = cents

