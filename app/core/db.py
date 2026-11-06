"""Conexão com o banco de dados (SQLAlchemy 2).

Em desenvolvimento usamos SQLite; em produção, PostgreSQL. O código é o mesmo.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from sqlalchemy import DateTime, Engine, MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import get_settings


def utcnow() -> datetime:
    """Agora em UTC, sem fuso (é assim que todas as datas ficam no banco)."""
    return datetime.now(UTC).replace(tzinfo=None)


# Nomes fixos para índices e restrições: deixa as migrações previsíveis
# (o SQLite precisa disso para alterar tabelas depois).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base de todas as tabelas."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """Colunas de criação e atualização presentes em todas as tabelas."""

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


def build_engine(url: str) -> Engine:
    """Cria a conexão com o banco a partir da URL."""
    if url.startswith("sqlite"):
        # Garante que a pasta do arquivo SQLite exista.
        db_path = url.split("///", 1)[-1]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _ativar_chaves_estrangeiras(dbapi_conn, _record):  # pragma: no cover - trivial
            # SQLite só respeita chaves estrangeiras com este comando.
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine
    return create_engine(url, pool_pre_ping=True)


@lru_cache
def get_engine() -> Engine:
    return build_engine(get_settings().database_url)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Dependência do FastAPI: abre uma sessão por requisição e fecha no fim."""
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()
