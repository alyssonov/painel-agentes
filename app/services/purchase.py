"""Sugestão de compra: quanto repor de cada produto para não faltar.

Conta simples e explicável (sem IA, custo zero):
  tem     = disponível no Olist + o que já foi pedido ao fornecedor (pedidos de compra "a caminho")
  precisa = vendas por dia × (prazo do fornecedor + dias de cobertura), no mínimo o estoque mínimo
  comprar = precisa − tem (arredondado para cima)

Prazo do fornecedor: o do produto (cadastro complementar) > o do fornecedor > o padrão do agente.
Urgência:
  "agora"  → acaba antes de a compra chegar (dura menos que o prazo) ou já está abaixo do mínimo
  "semana" → acaba até 7 dias depois do prazo do fornecedor
"""

import math
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product
from app.models.product_extra import ProductExtra
from app.models.purchase import PurchaseOrder, PurchaseOrderItem, Supplier
from app.services.stock import StockRow

URGENCY_LABELS = {"agora": ("Comprar já", "erro"), "semana": ("Comprar esta semana", "atencao")}
EXTRA_DAYS_FOR_WEEK = 7


@dataclass
class PurchaseItem:
    sku: str
    name: str
    available: int
    avg_daily: float
    days_left: float | None
    quantity: int
    unit_cost_cents: int
    urgency: str  # agora | semana
    on_order: int = 0  # já pedido ao fornecedor (a caminho)
    min_stock: int | None = None
    lead_days: float | None = None
    supplier: str | None = None

    @property
    def total_cost_cents(self) -> int:
        return self.quantity * self.unit_cost_cents


def on_order(db: Session) -> dict[str, int]:
    """Quantidade a caminho por SKU (pedidos de compra em aberto)."""
    totals: dict[str, int] = {}
    stmt = (select(PurchaseOrderItem.sku, PurchaseOrderItem.quantity)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.order_id)
            .where(PurchaseOrder.status == "aberto"))
    for sku, qty in db.execute(stmt):
        totals[sku.lower()] = totals.get(sku.lower(), 0) + qty
    return totals


def purchase_suggestions(db: Session, rows: list[StockRow], *, lead_days: float, coverage_days: float) -> list[PurchaseItem]:
    ids = [r.product_id for r in rows]
    costs = dict(db.execute(select(Product.id, Product.cost_cents).where(Product.id.in_(ids))).all()) if rows else {}
    extras = {e.product_id: e for e in db.scalars(select(ProductExtra).where(ProductExtra.product_id.in_(ids)))} \
        if rows else {}
    suppliers = {s.name.lower(): s for s in db.scalars(select(Supplier))}
    coming = on_order(db)
    items = []
    for row in rows:
        if row.available is None:
            continue  # sem leitura do Olist: nada a sugerir
        extra = extras.get(row.product_id)
        min_stock = extra.min_stock if extra else None
        supplier = suppliers.get((extra.supplier or "").lower()) if extra and extra.supplier else None
        lead = float((extra.lead_time_days if extra and extra.lead_time_days is not None else None)
                     or (supplier.lead_time_days if supplier and supplier.lead_time_days else None) or lead_days)
        ordered = coming.get(row.sku.lower(), 0)
        have = max(row.available, 0) + ordered
        if row.avg_daily <= 0 and not (min_stock and have < min_stock):
            continue  # sem vendas na janela e sem mínimo a repor
        days_left = have / row.avg_daily if row.avg_daily > 0 else None
        below_min = bool(min_stock and have < min_stock)
        if below_min or (days_left is not None and days_left <= lead):
            urgency = "agora"
        elif days_left is not None and days_left <= lead + EXTRA_DAYS_FOR_WEEK:
            urgency = "semana"
        else:
            continue
        need = max(row.avg_daily * (lead + coverage_days), float(min_stock or 0))
        quantity = math.ceil(need - have)
        if quantity <= 0:
            continue
        items.append(PurchaseItem(row.sku, row.name, max(row.available, 0), row.avg_daily, days_left, quantity,
                                  int(costs.get(row.product_id) or 0), urgency, ordered, min_stock, lead,
                                  extra.supplier if extra else None))
    items.sort(key=lambda i: (i.urgency != "agora", i.days_left if i.days_left is not None else 0, i.name))
    return items
