"""Envios dos marketplaces — situação e prazo de despacho (somente leitura)."""

from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class Shipment(TimestampMixin, Base):
    __tablename__ = "shipments"
    __table_args__ = (UniqueConstraint("channel", "shipment_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(20))
    shipment_id: Mapped[str] = mapped_column(String(32))
    # Número do pedido (ou do pacote) no marketplace — liga com o pedido do Olist.
    order_ref: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(30))  # pending | handling | ready_to_ship | shipped | delivered...
    substatus: Mapped[str | None] = mapped_column(String(40), nullable=True)
    logistic_type: Mapped[str | None] = mapped_column(String(30), nullable=True)  # fulfillment = Full
    # Prazo limite para despachar (UTC), lido do SLA do marketplace.
    dispatch_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime)
