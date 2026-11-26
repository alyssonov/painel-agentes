"""Configuração do Alembic: usa a mesma URL de banco e as mesmas tabelas do app."""

from logging.config import fileConfig

from alembic import context

import app.models  # noqa: F401 - registra todas as tabelas no metadata
from app.config import get_settings
from app.core.db import Base, build_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    # Permite que os testes passem outra URL; senão usa o .env.
    return config.attributes.get("database_url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = build_engine(_url())
    sqlite = engine.dialect.name == "sqlite"
    with engine.connect() as connection:
        if sqlite:
            # IMPORTANTE: no SQLite, alterar uma tabela = criar uma nova, copiar os dados e
            # APAGAR a antiga. Com as chaves estrangeiras ligadas, apagar a antiga apagaria
            # em cascata as linhas filhas (itens de pedido, histórico dos agentes...).
            # Por isso elas ficam desligadas durante a migração e são conferidas no fim.
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        # render_as_batch: necessário para alterar tabelas no SQLite.
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
        if sqlite:
            problems = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
            if problems:
                raise RuntimeError(f"Migração deixou referências quebradas no banco: {problems[:5]}")
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
