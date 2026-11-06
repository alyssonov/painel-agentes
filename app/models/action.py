"""Ações propostas pelos agentes (ou por pessoas) que falam com clientes ou mudam algo fora do painel.

Toda escrita externa vira uma Ação e só é executada pelo ActionGateway
(app/core/actions.py), depois de aprovada (ou pelas regras do modo automático).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.models.enums import ActionStatus


class Action(TimestampMixin, Base):
    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_slug: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(40), index=True)
    channel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    target_type: Mapped[str] = mapped_column(String(40))  # ex.: "pergunta"
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    # O que será enviado (ex.: {"text": "..."}). original_payload guarda a sugestão do agente.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    original_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[str | None] = mapped_column(String(10), nullable=True)  # alta | media | baixa
    topic: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    needs_human: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default=ActionStatus.PENDING, index=True)
    decided_by: Mapped[str | None] = mapped_column(String(80), nullable=True)  # "user:1" ou "agent:perguntas"
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    dry_run: Mapped[bool] = mapped_column(default=True)
