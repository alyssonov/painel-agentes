"""Agentes (configuração) e suas execuções."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.models.enums import AgentMode


class Agent(TimestampMixin, Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0)  # ordem no painel
    enabled: Mapped[bool] = mapped_column(default=False)  # Ativo / Pausado
    # Manual é o padrão: nenhuma ação sai sem aprovação humana.
    mode: Mapped[str] = mapped_column(String(20), default=AgentMode.MANUAL)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    runs: Mapped[list["AgentRun"]] = relationship(back_populates="agent", cascade="all, delete-orphan")


class AgentRun(TimestampMixin, Base):
    """Uma execução de um agente: quando rodou, quanto processou e o resumo."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20))  # ok | erro | rodando
    items_processed: Mapped[int] = mapped_column(Integer, default=0)
    # Frase curta mostrada no painel, ex.: "12 perguntas respondidas hoje".
    summary: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_demo: Mapped[bool] = mapped_column(default=False)

    agent: Mapped[Agent] = relationship(back_populates="runs")
