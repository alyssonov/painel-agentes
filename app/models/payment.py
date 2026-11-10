"""Pagamentos das vendas e quando o dinheiro é liberado (Mercado Livre → Mercado Pago). Só leitura."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("channel", "payment_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(20), index=True)
    payment_id: Mapped[str] = mapped_column(String(40))
    order_ref: Mapped[str] = mapped_column(String(64), index=True)  # pacote ou pedido no marketplace
    status: Mapped[str] = mapped_column(String(30), index=True)  # approved | refunded | cancelled | ...
    amount_cents: Mapped[int] = mapped_column(BigInteger, default=0)  # valor pago pelo comprador
    net_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # o que cai na conta (sem taxas)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Quando o dinheiro fica disponível (UTC) e se já foi liberado ("pending" | "released").
    release_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    release_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    is_demo: Mapped[bool] = mapped_column(default=False)
