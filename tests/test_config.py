"""Configurações padrão seguras."""

import pytest

from app.config import Settings


def test_modo_simulacao_e_o_padrao(monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)
    assert Settings(_env_file=None).dry_run is True


def test_falta_de_chaves_impede_inicio():
    s = Settings(_env_file=None, secret_key="", encryption_key="")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        s.check_ready()


def test_secret_key_curta_e_recusada():
    with pytest.raises(ValueError):
        Settings(_env_file=None, secret_key="curta")


def test_producao_exige_cookie_seguro():
    s = Settings(_env_file=None, app_env="production", secret_key="x" * 40,
                 encryption_key="k", session_cookie_secure=False)
    with pytest.raises(RuntimeError, match="SESSION_COOKIE_SECURE"):
        s.check_ready()
