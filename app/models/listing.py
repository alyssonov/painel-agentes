"""Anúncios nos marketplaces — espelho SOMENTE LEITURA (estoque, preço, SKU).

Um anúncio com variações (cor, tamanho...) vira uma linha por variação, cada
uma com o seu SKU e estoque, para comparar com o produto certo do Olist.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class Listing(TimestampMixin, Base):
    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("channel", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(20), index=True)
    # Id do anúncio (ex.: "MLB123") ou "MLB123-<variação>" quando é uma variação.
    external_id: Mapped[str] = mapped_column(String(64))
    item_id: Mapped[str] = mapped_column(String(32), index=True)  # anúncio "pai"
    sku: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"),
                                                   nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    variation_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    price_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    available_quantity: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20))  # active | paused | closed...
    # Estoque no armazém do marketplace (ex.: Mercado Livre Full): não é comparado com o Olist.
    fulfillment: Mapped[bool] = mapped_column(default=False)
    permalink: Mapped[str | None] = mapped_column(String(255), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime)
