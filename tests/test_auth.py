"""Login, bloqueio por tentativas, CSRF e páginas protegidas."""

from sqlalchemy import select

from app.models import AuditLog, User
from tests.conftest import csrf_from, login


def test_pagina_protegida_redireciona_para_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_htmx_sem_login_recebe_redirecionamento(client):
    r = client.get("/parciais/pedidos", headers={"HX-Request": "true"})
    assert r.headers.get("HX-Redirect") == "/login"


def test_login_correto(client, user, db):
    r = login(client)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert client.get("/").status_code == 200
    events = [e.event for e in db.scalars(select(AuditLog))]
    assert "login.ok" in events


def test_login_errado_mensagem_generica(client, user):
    r = login(client, password="errada12345")
    assert r.status_code == 401
    assert "E-mail ou senha incorretos" in r.text
    r2 = login(client, email="naoexiste@loja.com")
    assert r2.status_code == 401
    assert "E-mail ou senha incorretos" in r2.text  # não revela se o e-mail existe


def test_bloqueio_apos_varias_tentativas(client, user, db):
    for _ in range(5):
        login(client, password="errada12345")
    r = login(client)  # até a senha certa é recusada durante o bloqueio
    assert r.status_code == 429
    db.expire_all()
    assert db.scalar(select(User)).locked_until is not None


def test_login_sem_csrf_e_recusado(client, user):
    client.get("/login")
    r = client.post("/login", data={"email": "dono@loja.com", "password": "senhaTeste123"})
    assert r.status_code == 403


def test_logout(logged_client):
    token = csrf_from(logged_client.get("/").text)
    r = logged_client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert r.status_code == 303
    assert logged_client.get("/", follow_redirects=False).status_code == 303


def test_troca_de_senha_derruba_sessao(logged_client, db, user):
    from app.core.security import hash_password

    u = db.get(User, user.id)
    u.password_hash = hash_password("novaSenha999")
    db.commit()
    assert logged_client.get("/", follow_redirects=False).status_code == 303


def test_cabecalhos_de_seguranca(client):
    r = client.get("/login")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    cookie = r.headers.get("set-cookie", "")
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()


def test_saude(client):
    assert client.get("/saude").json() == {"status": "ok"}
