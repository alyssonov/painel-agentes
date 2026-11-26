"""Usuários do painel."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.models.enums import UserRole


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Empresa do usuário. Não usa o filtro automático: o login procura o e-mail em
    # todas as empresas (o e-mail é único no sistema) e daí define a empresa da sessão.
    company_id: Mapped[int] = mapped_column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), index=True,
                                            default=1, server_default="1")
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    # Apenas o hash Argon2 — a senha em si nunca é guardada.
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=UserRole.ADMIN)
    # Permissões marcadas à mão na tela Equipe. Vazio (None) = valem as do papel.
    permissions: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=None)
    is_active: Mapped[bool] = mapped_column(default=True)
    # Proteção contra tentativas de adivinhar a senha.
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def effective_permissions(self) -> frozenset[str]:
        """Permissões que valem para a pessoa (papel ou marcadas à mão)."""
        from app.core.permissions import permissions_of
        return permissions_of(self.role, self.permissions)

    def can(self, permission: str | None) -> bool:
        return permission is None or permission in self.effective_permissions
