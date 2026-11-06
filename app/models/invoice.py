"""Notas fiscais — espelho SOMENTE LEITURA do Olist.

O sistema nunca emite, cancela ou altera notas: só lê para conferir.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class Invoice(TimestampMixin, Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    olist_id: Mapped[str] = mapped_column(String(64), unique=True)
    # Pedido de origem no Olist (campo origem.id quando origem.tipo = "venda").
    order_olist_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"),
                                                 nullable=True, index=True)
    number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    series: Mapped[str | None] = mapped_column(String(10), nullable=True)
    access_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Situação no Olist (1 Pendente, 2 Emitida, 3 Cancelada, 5 Rejeitada, 6 Autorizada...).
    status_code: Mapped[int] = mapped_column()
    status_label: Mapped[str] = mapped_column(String(40))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    total_cents: Mapped[int] = mapped_column(BigInteger, default=0)
