"""Relatórios gerados e enviados (diário; semanal e mensal na próxima etapa)."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class Report(TimestampMixin, Base):
    __tablename__ = "reports"
    # Um relatório por tipo e período: impede envio duplicado.
    __table_args__ = (UniqueConstraint("kind", "period_start"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # diario | semanal | mensal
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # números usados no relatório
    status: Mapped[str] = mapped_column(String(20))  # enviado | falhou | sem_destinatario
    sent_to: Mapped[str | None] = mapped_column(Text, nullable=True)  # e-mails, separados por vírgula
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_by: Mapped[str] = mapped_column(String(80), default="system")  # system | user:<id>
