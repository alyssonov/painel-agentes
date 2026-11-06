"""Uso da Claude API: tokens e custo estimado de cada chamada."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow


class AiUsage(Base):
    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    agent_slug: Mapped[str] = mapped_column(String(40))
    task: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(60))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # Custo estimado em milésimos de centavo de real (precisão para chamadas baratas).
    cost_brl_millicents: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit: Mapped[bool] = mapped_column(default=False)
    is_demo: Mapped[bool] = mapped_column(default=False)
