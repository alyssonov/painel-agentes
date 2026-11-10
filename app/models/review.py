"""Avaliações dos produtos e análise de problemas que se repetem (Agente 6). Só leitura nos marketplaces.

Os textos das avaliações são opiniões públicas dos compradores, guardadas só no
banco (nunca nos logs) e sem nome de quem escreveu.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class ItemRating(TimestampMixin, Base):
    """Resumo das avaliações de um anúncio (média e quantidade por estrela)."""

    __tablename__ = "item_ratings"
    __table_args__ = (UniqueConstraint("channel", "item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(20), index=True)
    item_id: Mapped[str] = mapped_column(String(32))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    average: Mapped[float | None] = mapped_column(Float, nullable=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    low: Mapped[int] = mapped_column(Integer, default=0)  # 1 a 3 estrelas
    checked_at: Mapped[datetime] = mapped_column(DateTime)
    is_demo: Mapped[bool] = mapped_column(default=False)


class ProductReview(Base):
    """Avaliação negativa (1 a 3 estrelas) de um anúncio — base para achar problemas que se repetem."""

    __tablename__ = "product_reviews"
    __table_args__ = (UniqueConstraint("channel", "review_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(20), index=True)
    review_id: Mapped[str] = mapped_column(String(40))
    item_id: Mapped[str] = mapped_column(String(32), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True,
                                                   index=True)
    rate: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    variation: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    is_demo: Mapped[bool] = mapped_column(default=False)


class ClaimReason(Base):
    """Explicação de cada motivo de reclamação do Mercado Livre (cache de /claims/reasons/{id})."""

    __tablename__ = "claim_reasons"

    reason_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)


class ProductInsight(TimestampMixin, Base):
    """O que se repete nas avaliações e devoluções de um produto, resumido pela IA (semanal)."""

    __tablename__ = "product_insights"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), unique=True)
    summary: Mapped[str] = mapped_column(Text)
    problems: Mapped[list] = mapped_column(JSON, default=list)  # [{"problema": ..., "evidencia": ...}]
    suggestions: Mapped[list] = mapped_column(JSON, default=list)  # ["...", ...]
    inputs_hash: Mapped[str] = mapped_column(String(64))
    generated_at: Mapped[datetime] = mapped_column(DateTime)
    is_demo: Mapped[bool] = mapped_column(default=False)
