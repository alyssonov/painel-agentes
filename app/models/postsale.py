"""Pós-venda: conversas dos pedidos e reclamações (Mercado Livre).

Os textos das mensagens são dados de clientes: ficam só no banco e nunca vão
para os logs.
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.models.enums import ConversationStatus


class Conversation(TimestampMixin, Base):
    """Conversa pós-venda de um pacote/pedido."""

    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("channel", "pack_ref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(20), index=True)
    # Número do pacote (ou do pedido, quando não há pacote) — liga com o pedido do Olist.
    pack_ref: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default=ConversationStatus.NEW, index=True)
    # Situação no marketplace: "active" ou "blocked" (+ motivo do bloqueio).
    remote_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    claim_ref: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_from_buyer: Mapped[bool] = mapped_column(Boolean, default=False)
    max_chars: Mapped[int] = mapped_column(default=350)
    checked_at: Mapped[datetime] = mapped_column(DateTime)
    is_demo: Mapped[bool] = mapped_column(default=False)

    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation", order_by="ConversationMessage.sent_at", cascade="all, delete-orphan")


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (UniqueConstraint("conversation_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(64))
    from_seller: Mapped[bool] = mapped_column(Boolean, default=False)
    text: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Claim(TimestampMixin, Base):
    """Reclamação aberta contra a loja (somente leitura: a resolução é feita no marketplace)."""

    __tablename__ = "claims"
    __table_args__ = (UniqueConstraint("channel", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(20), index=True)
    external_id: Mapped[str] = mapped_column(String(32))
    order_ref: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(20), index=True)  # opened | closed
    type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    stage: Mapped[str | None] = mapped_column(String(20), nullable=True)  # claim | dispute | ...
    reason_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    problem: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_responsible: Mapped[str | None] = mapped_column(String(20), nullable=True)  # seller | buyer | mediator
    due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    affects_reputation: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Ações que o marketplace espera da loja: [{"action": ..., "mandatory": bool, "due_date": iso}]
    seller_actions: Mapped[list] = mapped_column(JSON, default=list)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remote_updated: Mapped[str | None] = mapped_column(String(40), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime)
    is_demo: Mapped[bool] = mapped_column(default=False)
