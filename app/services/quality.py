"""Qualidade por produto: nota das avaliações, reclamações/devoluções por motivo e taxa de problema.

Sem IA. Janela padrão de 90 dias. Reclamações de cancelamento (cancel_sale /
cancel_purchase) não contam como problema do produto.
Limitação: a reclamação aponta o número da VENDA; em vendas de carrinho (pacote)
o Olist guarda o número do pacote, então algumas reclamações podem ficar sem produto.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models import Claim, ClaimReason, ItemRating, Order, OrderItem, Product, ProductInsight, ProductReview
from app.models.enums import KanbanStatus

GROUPS = {"PDD": "Defeito ou diferente do anunciado", "PNR": "Não recebido"}
NOT_PRODUCT = {"cancel_sale", "cancel_purchase"}


def claim_group(reason_id: str | None) -> str:
    return GROUPS.get((reason_id or "")[:3], "Outros motivos")


@dataclass
class ProductQuality:
    product: Product
    units: int = 0
    claims: int = 0
    by_group: dict[str, int] = field(default_factory=dict)
    reasons: dict[str, int] = field(default_factory=dict)  # explicação do motivo → quantidade
    rating_average: float | None = None
    rating_total: int = 0
    low_reviews: list[ProductReview] = field(default_factory=list)
    insight: ProductInsight | None = None

    @property
    def claim_rate(self) -> float | None:
        return self.claims / self.units * 100 if self.units else None

    @property
    def product_problems(self) -> int:
        return self.by_group.get(GROUPS["PDD"], 0)

    @property
    def score(self) -> float:
        """Para ordenar: quem mais pede atenção primeiro."""
        return self.claims * 2 + len(self.low_reviews) + (5 - (self.rating_average or 5)) * 3


def product_quality(db: Session, *, days: int = 90, demo: bool = False) -> list[ProductQuality]:
    since = utcnow() - timedelta(days=days)
    result: dict[int, ProductQuality] = {}

    def entry(product_id: int) -> ProductQuality | None:
        if product_id not in result:
            product = db.get(Product, product_id)
            if product is None:
                return None
            result[product_id] = ProductQuality(product)
        return result[product_id]

    for product_id, units in db.execute(
            select(OrderItem.product_id, func.sum(OrderItem.quantity)).join(Order, Order.id == OrderItem.order_id)
            .where(Order.ordered_at >= since, Order.is_demo.is_(demo), Order.kanban_status != KanbanStatus.CANCELLED,
                   OrderItem.product_id.is_not(None)).group_by(OrderItem.product_id)).all():
        e = entry(product_id)
        if e:
            e.units = int(units or 0)

    reasons = {r.reason_id: r.detail or r.name for r in db.scalars(select(ClaimReason))}
    for claim in db.scalars(select(Claim).where(Claim.is_demo.is_(demo), Claim.opened_at >= since)):
        if claim.type in NOT_PRODUCT or not claim.order_ref:
            continue
        order = db.scalar(select(Order).where(Order.external_id == claim.order_ref, Order.is_demo.is_(demo)))
        if order is None:
            continue
        for item in order.items:
            if item.product_id is None:
                continue
            e = entry(item.product_id)
            if e is None:
                continue
            e.claims += 1
            group = claim_group(claim.reason_id)
            e.by_group[group] = e.by_group.get(group, 0) + 1
            label = reasons.get(claim.reason_id) or group
            e.reasons[label] = e.reasons.get(label, 0) + 1

    for rating in db.scalars(select(ItemRating).where(ItemRating.is_demo.is_(demo), ItemRating.product_id.is_not(None))):
        e = entry(rating.product_id)
        if e is None or rating.average is None or not rating.total:
            continue
        total = e.rating_total + rating.total
        e.rating_average = ((e.rating_average or 0) * e.rating_total + rating.average * rating.total) / total
        e.rating_total = total
    for review in db.scalars(select(ProductReview).where(ProductReview.is_demo.is_(demo), ProductReview.created_at >= since,
                                                         ProductReview.product_id.is_not(None))
                             .order_by(ProductReview.created_at.desc())):
        e = entry(review.product_id)
        if e:
            e.low_reviews.append(review)
    for insight in db.scalars(select(ProductInsight).where(ProductInsight.product_id.in_(list(result)))):
        result[insight.product_id].insight = insight

    rows = [q for q in result.values() if q.claims or q.low_reviews or (q.rating_average and q.rating_average < 4.5)]
    rows.sort(key=lambda q: -q.score)
    return rows
