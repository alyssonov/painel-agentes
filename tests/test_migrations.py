"""As migrações do Alembic criam exatamente as tabelas dos modelos."""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from app.core.db import Base, build_engine


def test_migracoes_batem_com_modelos(tmp_path):
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")
    engine = build_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == [], f"Modelos e migrações diferentes: {diff}"
