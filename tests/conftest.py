"""Configuração comum dos testes: banco SQLite novo por teste, chaves de teste."""

import os
import re

# Variáveis definidas ANTES de importar o app (valem mais que o .env).
os.environ.update({
    "APP_ENV": "test",
    "DRY_RUN": "true",
    "SECRET_KEY": "chave-de-teste-com-mais-de-32-caracteres-ok",
    "ENCRYPTION_KEY": "x2bS2sKTq2mXqKfQyB4Fv2m3o2jD0JmZ8n7aTn3v6PQ=",
    "SESSION_COOKIE_SECURE": "false",
    "DATABASE_URL": "sqlite:///:memory:",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.models  # noqa: E402,F401
from app.config import get_settings  # noqa: E402
from app.core import db as db_module  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models import User  # noqa: E402

TEST_PASSWORD = "senhaTeste123"


def _clear_caches() -> None:
    get_settings.cache_clear()
    db_module.get_engine.cache_clear()
    db_module.get_sessionmaker.cache_clear()


@pytest.fixture
def db_url(tmp_path, monkeypatch):
    """Cada teste ganha um banco SQLite novo, num arquivo temporário."""
    url = f"sqlite:///{tmp_path / 'teste.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    _clear_caches()
    db_module.Base.metadata.create_all(db_module.get_engine())
    yield url
    db_module.get_engine().dispose()
    _clear_caches()


@pytest.fixture
def db(db_url):
    session = db_module.get_sessionmaker()()
    yield session
    session.close()


@pytest.fixture
def user(db):
    u = User(email="dono@loja.com", name="Dono", password_hash=hash_password(TEST_PASSWORD))
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def client(db_url):
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "formulário sem token CSRF"
    return match.group(1)


def login(client: TestClient, email: str = "dono@loja.com", password: str = TEST_PASSWORD):
    token = csrf_from(client.get("/login").text)
    return client.post("/login", data={"csrf_token": token, "email": email, "password": password},
                       follow_redirects=False)


@pytest.fixture
def logged_client(client, user):
    response = login(client)
    assert response.status_code == 303
    return client
