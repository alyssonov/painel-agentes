"""Multiempresa: cada empresa só vê e só grava os próprios dados."""

import sqlite3

import pytest
from sqlalchemy import select

from app.core import db as db_module
from app.core.backup import backup_before_migrate
from app.core.security import hash_password
from app.core.tenant import CrossCompanyWrite, company_of, set_company
from app.models import AuditLog, Company, Product, StoreSetting, User
from app.models.company import DEFAULT_COMPANY_ID
from app.services.store_settings import get_setting, set_setting
from tests.conftest import TEST_PASSWORD, login


def _session(company_id=None):
    s = db_module.get_sessionmaker()()
    if company_id:
        set_company(s, company_id)
    return s


@pytest.fixture
def two_companies(db):
    db.add(Company(id=2, name="Outra loja"))
    db.commit()
    with _session(1) as a:
        a.add(Product(sku="SKU-1", name="Pastilha da loja 1", cost_cents=100))
        set_setting(a, "tax_rate_pct", 12.0)
        a.commit()
    with _session(2) as b:
        b.add(Product(sku="SKU-1", name="Pastilha da loja 2", cost_cents=200))  # mesmo SKU: permitido
        b.commit()


def test_banco_novo_ja_tem_a_empresa_padrao(db):
    assert db.get(Company, DEFAULT_COMPANY_ID).name == "Minha loja"
    assert company_of(db) == DEFAULT_COMPANY_ID


def test_cada_empresa_so_ve_os_proprios_dados(two_companies):
    with _session(1) as a, _session(2) as b:
        assert [p.name for p in a.scalars(select(Product))] == ["Pastilha da loja 1"]
        assert [p.name for p in b.scalars(select(Product))] == ["Pastilha da loja 2"]
        assert b.query(Product).count() == 1
        assert get_setting(a, "tax_rate_pct") == 12.0 and get_setting(b, "tax_rate_pct") == 0.0
        # get() por id também respeita a empresa
        other_id = a.scalar(select(Product.id))
        assert b.get(Product, other_id) is None
        # ver todas as empresas só pedindo explicitamente
        assert len(b.scalars(select(Product).execution_options(all_companies=True)).all()) == 2


def test_registros_novos_recebem_a_empresa_e_nao_cruzam(two_companies):
    with _session(2) as b:
        b.add(StoreSetting(key="answer_tone", value="direto"))
        b.commit()
        assert b.scalar(select(StoreSetting).where(StoreSetting.key == "answer_tone")).company_id == 2
        b.add(Product(sku="X", name="Intruso", cost_cents=1, company_id=1))
        with pytest.raises(CrossCompanyWrite):
            b.flush()
        b.rollback()
    with _session(2) as b:  # update/delete em massa também ficam presos à empresa
        b.query(Product).update({Product.cost_cents: 999})
        b.commit()
    with _session(1) as a:
        assert a.scalar(select(Product.cost_cents)) == 100


def test_usuario_ve_so_a_empresa_dele_no_painel(client, db, two_companies):
    db.add(User(email="dono2@outra.com", name="Dono 2", company_id=2, password_hash=hash_password(TEST_PASSWORD)))
    db.commit()
    assert login(client, "dono2@outra.com").status_code == 303
    html = client.get("/estoque-precos").text
    assert "Pastilha da loja 1" not in html
    with _session(2) as b:
        assert b.scalars(select(AuditLog).where(AuditLog.event == "login.ok")).one().company_id == 2
    with _session(1) as a:
        assert a.scalars(select(AuditLog).where(AuditLog.event == "login.ok")).all() == []


def test_copia_de_seguranca_antes_de_atualizar(tmp_path):
    db_file = tmp_path / "painel.db"
    with sqlite3.connect(db_file) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32))")
        conn.execute("INSERT INTO alembic_version VALUES ('0001')")
    copy = backup_before_migrate(f"sqlite:///{db_file}")
    assert copy is not None and copy.exists() and "antes-da-versao-" in copy.name
    with sqlite3.connect(copy) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("0001",)
    assert backup_before_migrate("sqlite:///:memory:") is None
    assert backup_before_migrate(f"sqlite:///{tmp_path / 'nao-existe.db'}") is None
