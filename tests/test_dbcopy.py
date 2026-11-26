"""Cópia do banco do computador (SQLite) para o servidor: tudo, na ordem certa, com segurança."""

from datetime import datetime

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.db import Base
from app.core.dbcopy import CopyError, copy_database
from app.models import AuditLog, Company, User


def _new_db(path) -> str:
    url = f"sqlite:///{path}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    engine.dispose()
    return url


def _fill(url: str) -> None:
    engine = create_engine(url)
    with Session(engine, info={"company_id": 1}) as s:
        s.add(User(email="dono@loja.com", name="Dono", password_hash="x", company_id=1))
        s.add(AuditLog(actor="system", event="teste", details={"a": [1, 2]}, at=datetime(2026, 11, 1, 12, 0),
                       company_id=1))
        s.commit()
    engine.dispose()


def test_copia_tudo_com_os_tipos_certos(tmp_path):
    src, dst = _new_db(tmp_path / "a.db"), _new_db(tmp_path / "b.db")
    _fill(src)
    counts = copy_database(src, dst)
    assert counts["users"] == 1 and counts["audit_log"] == 1 and counts["companies"] == 1
    engine = create_engine(dst)
    with engine.connect() as conn:
        row = conn.execute(select(AuditLog.__table__)).mappings().one()
        assert row["details"] == {"a": [1, 2]} and row["at"] == datetime(2026, 11, 1, 12, 0)
        assert conn.execute(select(User.__table__.c.email)).scalar() == "dono@loja.com"
    engine.dispose()


def test_destino_com_dados_so_com_confirmacao(tmp_path):
    src, dst = _new_db(tmp_path / "a.db"), _new_db(tmp_path / "b.db")
    _fill(src)
    _fill(dst)
    with pytest.raises(CopyError, match="já tem dados"):
        copy_database(src, dst)
    assert copy_database(src, dst, apagar_destino=True)["users"] == 1
    engine = create_engine(dst)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM users")).scalar() == 1
    engine.dispose()


def test_empresa_padrao_sozinha_conta_como_vazio(tmp_path):
    src, dst = _new_db(tmp_path / "a.db"), _new_db(tmp_path / "b.db")
    _fill(src)
    engine = create_engine(dst)
    with engine.connect() as conn:  # banco novo já nasce com a empresa padrão
        assert conn.execute(select(Company.__table__.c.id)).all() == [(1,)]
    engine.dispose()
    assert copy_database(src, dst)["companies"] == 1


def test_versoes_diferentes_nao_copia(tmp_path):
    src, dst = _new_db(tmp_path / "a.db"), _new_db(tmp_path / "b.db")
    engine = create_engine(src)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        conn.execute(text("INSERT INTO alembic_version VALUES ('0023')"))
    engine.dispose()
    with pytest.raises(CopyError, match="versões"):
        copy_database(src, dst)
