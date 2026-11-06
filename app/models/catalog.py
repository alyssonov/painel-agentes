"""Produtos e estoque — espelho somente leitura do Olist ERP."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class Product(TimestampMixin, Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    olist_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    specs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # ficha técnica
    # Dinheiro sempre em centavos (inteiro) para não haver erro de arredondamento.
    cost_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    price_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    is_active: Mapped[bool] = mapped_column(default=True)
    is_demo: Mapped[bool] = mapped_column(default=False)


class StockSnapshot(TimestampMixin, Base):
    """Saldo de estoque lido do Olist em um momento (nunca alteramos o estoque)."""

    __tablename__ = "stock_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    captured_at: Mapped[datetime] = mapped_column(DateTime, index=True)
