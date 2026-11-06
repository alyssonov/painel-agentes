"""Regras da loja editáveis pelo painel (tom das respostas, prazos, imposto...)."""

from typing import Any

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class StoreSetting(TimestampMixin, Base):
    __tablename__ = "store_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    value: Mapped[Any] = mapped_column(JSON)
