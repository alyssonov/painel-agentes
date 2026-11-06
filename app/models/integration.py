"""Contas conectadas (Olist, Mercado Livre, Shopee) e seus tokens OAuth."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.models.enums import IntegrationStatus


class IntegrationAccount(TimestampMixin, Base):
    __tablename__ = "integration_accounts"
    __table_args__ = (UniqueConstraint("provider", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), index=True)
    account_name: Mapped[str] = mapped_column(String(120))
    # seller_id (ML), shop_id (Shopee) ou identificador da empresa (Olist).
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Tokens SEMPRE criptografados (ver app/core/security.py). Nunca em texto puro.
    access_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=IntegrationStatus.DISCONNECTED)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Dados extras não sensíveis (ex.: reputação do vendedor).
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # true quando a conta foi criada pelos dados de simulação.
    is_demo: Mapped[bool] = mapped_column(default=False)
