"""Regras da loja editáveis pelo painel (tom das respostas, prazos, imposto...)."""

from typing import Any

from sqlalchemy import JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.models.company import CompanyScoped


class StoreSetting(CompanyScoped, TimestampMixin, Base):
    __tablename__ = "store_settings"
    __table_args__ = (UniqueConstraint("company_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(80))
    value: Mapped[Any] = mapped_column(JSON)
