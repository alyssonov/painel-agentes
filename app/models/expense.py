"""Despesas da empresa (aluguel, salários, contador...) — para o lucro líquido e o DRE.

Ficam só no Gerente do Ano (lançadas pela própria loja). Despesa mensal vale de
`start_month` até `end_month` (vazio = sem fim); despesa única vale só em `start_month`.
"""

from datetime import date

from sqlalchemy import BigInteger, Date, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.models.company import CompanyScoped

CATEGORIES = {
    "aluguel": "Aluguel e condomínio", "pessoal": "Salários e encargos", "contador": "Contador",
    "sistemas": "Sistemas e assinaturas", "embalagens": "Embalagens e materiais", "publicidade": "Publicidade",
    "energia": "Energia, água e internet", "transporte": "Transporte e combustível", "taxas": "Taxas bancárias",
    "outros": "Outras despesas",
}


class Expense(CompanyScoped, TimestampMixin, Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(30), default="outros")
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    recurring: Mapped[bool] = mapped_column(default=True)  # todo mês × só uma vez
    start_month: Mapped[date] = mapped_column(Date, index=True)  # sempre dia 1
    end_month: Mapped[date | None] = mapped_column(Date, nullable=True)  # último mês (dia 1); vazio = sem fim
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def applies_to(self, month: date) -> bool:
        """Vale para o mês (dia 1)?"""
        if not self.recurring:
            return self.start_month == month
        return self.start_month <= month and (self.end_month is None or month <= self.end_month)
