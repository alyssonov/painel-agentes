"""Referências de preço do Mercado Livre por anúncio (somente leitura) e a análise do agente de Preços."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class PriceReference(TimestampMixin, Base):
    __tablename__ = "price_references"
    __table_args__ = (UniqueConstraint("channel", "item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(20), index=True)
    item_id: Mapped[str] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    # with_benchmark_high(est) = acima da concorrência; no_benchmark_lowest = abaixo; no_benchmark_ok = no ponto
    status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    applicable: Mapped[bool] = mapped_column(default=False)
    current_cents: Mapped[int] = mapped_column(BigInteger, default=0)  # preço de venda atual (com promoção)
    listed_cents: Mapped[int] = mapped_column(BigInteger, default=0)  # preço cadastrado no anúncio
    suggested_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    lowest_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    # Custos informados pelo ML para o PREÇO SUGERIDO (confirmado com dados reais em 11/11/2026).
    selling_fee_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    shipping_fee_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    compared: Mapped[int] = mapped_column(default=0)
    automated: Mapped[bool] = mapped_column(default=False)  # automatização de preços do ML ligada
    has_variations: Mapped[bool] = mapped_column(default=False)
    # Conclusão do agente (texto simples para o painel) e lucro estimado no preço sugerido.
    verdict: Mapped[str | None] = mapped_column(String(30), nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    profit_at_suggested_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime)
    is_demo: Mapped[bool] = mapped_column(default=False)

    @property
    def promoted(self) -> bool:
        """Está vendendo com promoção (preço de venda diferente do cadastrado)?"""
        return bool(self.listed_cents and abs(self.listed_cents - self.current_cents) > 1)
