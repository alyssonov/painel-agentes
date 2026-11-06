"""Tela de contas conectadas e o fluxo de conexão do Olist."""

from urllib.parse import parse_qs, urlparse

import httpx
import respx
from sqlalchemy import select

from app.fake_data.seed import seed_demo
from app.integrations.olist.auth import TOKEN_URL
from app.integrations.olist.client import BASE_URL
from app.integrations.tokens import read_access_token
from app.models import Agent, AuditLog, IntegrationAccount, Order
from app.models.enums import IntegrationStatus
from app.services.store_settings import get_setting
from tests.conftest import csrf_from
from tests.olist_helpers import connect_account


def test_sem_chaves_mostra_como_configurar(logged_client):
    html = logged_client.get("/integracoes").text
    assert "Falta configurar as chaves" in html
    assert "OLIST_CLIENT_ID" in html
    r = logged_client.get("/integracoes/olist/conectar", follow_redirects=False)
    assert r.headers["location"] == "/integracoes"


def test_conectar_leva_ao_olist_com_state(logged_client, olist_env):
    r = logged_client.get("/integracoes/olist/conectar", follow_redirects=False)
    url = urlparse(r.headers["location"])
    assert url.netloc == "accounts.tiny.com.br"
    assert len(parse_qs(url.query)["state"][0]) > 20


def test_retorno_com_state_errado_e_recusado(logged_client, olist_env, db):
    logged_client.get("/integracoes/olist/conectar", follow_redirects=False)
    r = logged_client.get("/integracoes/olist/retorno?code=abc&state=forjado")
    assert "Não foi possível confirmar" in r.text
    assert db.scalar(select(IntegrationAccount).where(IntegrationAccount.is_demo.is_(False))) is None


def test_retorno_exige_login(client, olist_env):
    r = client.get("/integracoes/olist/retorno?code=abc&state=x", follow_redirects=False)
    assert r.headers["location"] == "/login"


@respx.mock
def test_conexao_completa(logged_client, olist_env, db, monkeypatch):
    synced = []
    monkeypatch.setattr("app.web.routes.integrations._sync_in_background", lambda: synced.append(1))
    seed_demo(db, rng_seed=1)  # painel estava na simulação
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={
        "access_token": "tok-real", "refresh_token": "ref-real", "expires_in": 14400,
        "refresh_expires_in": 86400}))
    respx.get(f"{BASE_URL}/info").mock(return_value=httpx.Response(200, json={
        "fantasia": "Loja do Dono", "cpfCnpj": "12.345.678/0001-90"}))

    r = logged_client.get("/integracoes/olist/conectar", follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = logged_client.get(f"/integracoes/olist/retorno?code=codigo&state={state}")
    assert "Olist conectado" in r.text
    assert "Loja do Dono" in r.text
    assert "mostrando dados reais" in r.text  # a faixa deixa de dizer "dados fictícios"

    db.expire_all()
    account = db.scalar(select(IntegrationAccount).where(IntegrationAccount.is_demo.is_(False)))
    assert account.status == IntegrationStatus.OK
    assert "tok-real" not in account.access_token_enc and read_access_token(account) == "tok-real"
    # A loja fictícia saiu, os agentes voltaram a "Pausado" e a alíquota de exemplo foi zerada.
    assert db.query(Order).count() == 0
    assert db.scalar(select(IntegrationAccount).where(IntegrationAccount.is_demo.is_(True))) is None
    assert not any(a.enabled for a in db.scalars(select(Agent)))
    assert get_setting(db, "tax_rate_pct") == 0
    assert synced == [1]  # importação iniciada
    assert "olist.conectado" in [e.event for e in db.scalars(select(AuditLog))]

    # A simulação não volta mais enquanto houver conta real conectada.
    from app.fake_data.seed import refresh_demo_if_stale
    assert refresh_demo_if_stale(db) is False


def test_desconectar_apaga_tokens(logged_client, olist_env, db):
    connect_account(db)
    token = csrf_from(logged_client.get("/integracoes").text)
    logged_client.post("/integracoes/olist/desconectar", data={"csrf_token": token})
    db.expire_all()
    account = db.scalar(select(IntegrationAccount).where(IntegrationAccount.is_demo.is_(False)))
    assert account.access_token_enc is None and account.refresh_token_enc is None
    assert account.status == IntegrationStatus.DISCONNECTED


def test_botoes_exigem_csrf(logged_client, olist_env, db):
    connect_account(db)
    assert logged_client.post("/integracoes/olist/desconectar").status_code == 403
    assert logged_client.post("/integracoes/olist/sincronizar").status_code == 403
    assert logged_client.post("/integracoes/imposto", data={"tax_rate_pct": "10"}).status_code == 403


def test_atualizar_agora(logged_client, olist_env, db, monkeypatch):
    synced = []
    monkeypatch.setattr("app.web.routes.integrations._sync_in_background", lambda: synced.append(1))
    connect_account(db)
    token = csrf_from(logged_client.get("/integracoes").text)
    r = logged_client.post("/integracoes/olist/sincronizar", data={"csrf_token": token})
    assert "Atualização iniciada" in r.text and synced == [1]


def test_salvar_aliquota(logged_client, db):
    token = csrf_from(logged_client.get("/integracoes").text)
    r = logged_client.post("/integracoes/imposto", data={"csrf_token": token, "tax_rate_pct": "14,25"})
    assert "14,25%" in r.text
    db.expire_all()
    assert get_setting(db, "tax_rate_pct") == 14.25
    r = logged_client.post("/integracoes/imposto", data={"csrf_token": token, "tax_rate_pct": "abc"})
    assert "entre 0 e 60" in r.text
    assert get_setting(db, "tax_rate_pct") == 14.25
