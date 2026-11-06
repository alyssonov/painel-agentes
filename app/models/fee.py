"""Taxas cobradas pelos marketplaces em cada venda.

O Olist não informa a taxa do marketplace. Ela vem da API do Mercado Livre
(e, mais adiante, da Shopee) e é ligada ao pedido do Olist pelo número do
pedido no marketplace. Guardar aqui permite ligar mesmo quando o pedido chega
no Olist depois.
"""

from sqlalchemy import BigInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class MarketplaceFee(TimestampMixin, Base):
    __tablename__ = "marketplace_fees"
    __table_args__ = (UniqueConstraint("channel", "order_ref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(20))
    # Número do pedido (ou do pacote) no marketplace.
    order_ref: Mapped[str] = mapped_column(String(64), index=True)
    fee_cents: Mapped[int] = mapped_column(BigInteger, default=0)
