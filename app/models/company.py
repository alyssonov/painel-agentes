"""Empresas (multiempresa): cada registro do painel pertence a uma empresa.

Hoje existe uma empresa só (a loja). A estrutura já separa os dados para o
Gerente do Ano poder atender várias lojas no futuro (decisão de 26/11/2026,
PENDENCIAS A4). O filtro automático por empresa fica em `app/core/tenant.py`.
"""

from sqlalchemy import DDL, ForeignKey, Integer, String, event
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.core.db import Base, TimestampMixin

# Empresa criada na instalação (a loja de hoje). Todo dado antigo pertence a ela.
DEFAULT_COMPANY_ID = 1


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    document: Mapped[str | None] = mapped_column(String(20), nullable=True)  # CNPJ, só números
    is_active: Mapped[bool] = mapped_column(default=True)


# Banco criado do zero (testes, instalação nova sem Alembic): já nasce com a empresa padrão.
event.listen(Company.__table__, "after_create", DDL(
    "INSERT INTO companies (id, name, is_active, created_at, updated_at) "
    f"VALUES ({DEFAULT_COMPANY_ID}, 'Minha loja', true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))


class CompanyScoped:
    """Mistura para tabelas cujos dados pertencem a uma empresa.

    As consultas dessas tabelas são filtradas automaticamente pela empresa da
    sessão, e registros novos recebem a empresa sozinhos (ver app/core/tenant.py).
    """

    @declared_attr
    def company_id(cls) -> Mapped[int]:  # noqa: N805 - padrão do SQLAlchemy
        return mapped_column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), index=True,
                             nullable=False, default=None)
