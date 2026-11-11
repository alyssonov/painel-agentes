"""Qualidade dos anúncios (Mercado Livre /performance) — base do agente de Anúncios. Só leitura."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class ListingQuality(TimestampMixin, Base):
    __tablename__ = "listing_quality"
    __table_args__ = (UniqueConstraint("channel", "item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(20), index=True)
    item_id: Mapped[str] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0 a 100
    level: Mapped[str | None] = mapped_column(String(20), nullable=True)  # Bad | Standard | Good...
    level_wording: Mapped[str | None] = mapped_column(String(30), nullable=True)  # Básica | Satisfatória | Profissional
    # O que falta, como o ML informa: [{"bucket", "label", "title", "link"}]
    pending: Mapped[list] = mapped_column(JSON, default=list)
    sold_quantity: Mapped[int] = mapped_column(Integer, default=0)  # com vendas o título não pode mudar
    checked_at: Mapped[datetime] = mapped_column(DateTime)
    is_demo: Mapped[bool] = mapped_column(default=False)
