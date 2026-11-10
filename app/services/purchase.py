"""Sugestão de compra: quanto repor de cada produto para não faltar.

Conta simples e explicável (sem IA, custo zero):
  precisa = vendas por dia × (prazo do fornecedor + dias de cobertura)
  comprar = precisa − disponível no Olist (arredondado para cima)

Urgência:
  "agora"  → acaba antes de a compra chegar (dura menos que o prazo do fornecedor)
  "semana" → acaba até 7 dias depois do prazo do fornecedor
Não considera pedidos de compra já feitos ao fornecedor (o Olist é só lido).
"""

import math
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product
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

    @property
    def total_cost_cents(self) -> int:
        return self.quantity * self.unit_cost_cents


def purchase_suggestions(db: Session, rows: list[StockRow], *, lead_days: float, coverage_days: float) -> list[PurchaseItem]:
    costs = dict(db.execute(select(Product.id, Product.cost_cents).where(
        Product.id.in_([r.product_id for r in rows]))).all()) if rows else {}
    items = []
    for row in rows:
        if row.available is None or row.avg_daily <= 0:
            continue  # sem leitura do Olist ou sem vendas na janela: nada a sugerir
        available = max(row.available, 0)
        days_left = available / row.avg_daily
        if days_left <= lead_days:
            urgency = "agora"
        elif days_left <= lead_days + EXTRA_DAYS_FOR_WEEK:
            urgency = "semana"
        else:
            continue
        quantity = math.ceil(row.avg_daily * (lead_days + coverage_days) - available)
        if quantity <= 0:
            continue
        items.append(PurchaseItem(row.sku, row.name, available, row.avg_daily, days_left, quantity,
                                  int(costs.get(row.product_id) or 0), urgency))
    items.sort(key=lambda i: (i.urgency != "agora", i.days_left if i.days_left is not None else 0, i.name))
    return items
