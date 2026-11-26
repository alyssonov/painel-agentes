"""Histórico das simulações de preço (últimas 10 por pessoa)."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.company import CompanyScoped


class PricingSimulation(CompanyScoped, Base):
    __tablename__ = "pricing_simulations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    query: Mapped[str] = mapped_column(Text)  # parâmetros do formulário, para refazer a simulação
    summary: Mapped[list] = mapped_column(JSON, default=list)  # [{"name", "price_cents", "margin_pct"}]
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
