"""Automações: avisos por e-mail montados a partir dos alertas do painel.

As automações são fixas no código (`app/automations/catalog.py`); aqui fica só o
que a loja escolheu (ligada, parâmetros) e o registro de cada alerta já avisado,
para nunca mandar o mesmo aviso duas vezes.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.models.company import CompanyScoped


class Automation(CompanyScoped, TimestampMixin, Base):
    __tablename__ = "automations"
    __table_args__ = (UniqueConstraint("company_id", "slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(40))
    enabled: Mapped[bool] = mapped_column(default=False)
    # Desde quando está ligada: alertas mais antigos não são enviados (evita uma enxurrada).
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AutomationDelivery(CompanyScoped, TimestampMixin, Base):
    """Alerta já avisado por uma automação (um aviso por alerta, nunca repetido)."""

    __tablename__ = "automation_deliveries"
    __table_args__ = (UniqueConstraint("company_id", "automation_id", "alert_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    automation_id: Mapped[int] = mapped_column(ForeignKey("automations.id", ondelete="CASCADE"), index=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), index=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime)
