"""Conversas com o Assistente (chat de IA do painel). Cada pessoa vê só as suas."""

from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.models.company import CompanyScoped


class AssistantConversation(CompanyScoped, TimestampMixin, Base):
    __tablename__ = "assistant_conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120))

    messages: Mapped[list["AssistantMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="AssistantMessage.id")


class AssistantMessage(CompanyScoped, TimestampMixin, Base):
    __tablename__ = "assistant_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("assistant_conversations.id", ondelete="CASCADE"),
                                                 index=True)
    role: Mapped[str] = mapped_column(String(12))  # "user" | "assistant"
    text: Mapped[str] = mapped_column(Text)
    # Tabelas da resposta (para baixar como planilha): [{"title": ..., "header": [...], "rows": [[...]]}]
    tables: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    tools_used: Mapped[list[str]] = mapped_column(JSON, default=list)  # quais consultas a IA fez
    cost_brl_millicents: Mapped[int] = mapped_column(Integer, default=0)

    conversation: Mapped[AssistantConversation] = relationship(back_populates="messages")
