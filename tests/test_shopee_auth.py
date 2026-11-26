"""Shopee — autorização oficial (link, assinatura, troca do código, renovação, telas)."""

import hashlib
import hmac
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from sqlalchemy import select

from app.config import get_settings
from app.core.db import get_sessionmaker, utcnow
from app.integrations.errors import AuthExpired, IntegrationError
from app.integrations.shopee import auth
from app.integrations.tokens import TokenSet, read_access_token, read_refresh_token, store_tokens
from app.models import IntegrationAccount
from app.models.enums import IntegrationStatus, Provider
from app.sync.shopee_sync import keep_shopee_alive
from tests.conftest import csrf_from

PARTNER_ID = "2001234"
PARTNER_KEY = "chave-de-teste-shopee"
HOST = "https://partner.shopeemobile.com"


@pytest.fixture
def shopee_env(monkeypatch):
    monkeypatch.setenv("SHOPEE_PARTNER_ID", PARTNER_ID)
    monkeypatch.setenv("SHOPEE_PARTNER_KEY", PARTNER_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _token_response(access="acesso-1", refresh="renova-1", **extra):
    return httpx.Response(200, json={"access_token": access, "refresh_token": refresh, "expire_in": 14400,
                                     "error": "", "message": "", "request_id": "r1", **extra})


def connect_shopee(db, shop_id="54804", expires_in=14400):
    account = IntegrationAccount(provider=Provider.SHOPEE, account_name="Loja Shopee", is_demo=False,
                                 external_id=shop_id, status=IntegrationStatus.OK, extra={})
    store_tokens(account, TokenSet("acesso-0", "renova-0", expires_in, 30 * 86400))
    db.add(account)
    db.commit()
    return account


def test_assinatura_igual_a_da_documentacao(shopee_env):
    expected = hmac.new(PARTNER_KEY.encode(), f"{PARTNER_ID}/api/v2/auth/token/get1657263479".encode(),
                        hashlib.sha256).hexdigest()
    assert auth.sign("/api/v2/auth/token/get", 1657263479) == expected
    shop = hmac.new(PARTNER_KEY.encode(), f"{PARTNER_ID}/api/v2/shop/get_shop_info1657263479tok54804".encode(),
                    hashlib.sha256).hexdigest()
    assert auth.sign("/api/v2/shop/get_shop_info", 1657263479, "tok", 54804) == shop
    assert shop == shop.lower()


def test_link_de_autorizacao_do_brasil(shopee_env):
    url = urlparse(auth.authorize_url("estado-x"))
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://open.shopee.com.br/auth"
    q = parse_qs(url.query)
    assert q["partner_id"] == [PARTNER_ID] and q["auth_type"] == ["seller"] and q["response_type"] == ["code"]
    assert q["redirect_uri"] == ["https://alyssonov.github.io/painel-retorno/shopee/"] and q["state"] == ["estado-x"]


def test_validade_aceita_segundos_ou_horario():
    assert auth._expires_in(14400) == 14400
    assert 0 < auth._expires_in(int(utcnow().timestamp()) + 3600 + 10 ** 5) and auth._expires_in(None) == 14400


@respx.mock
def test_conectar_ponta_a_ponta(logged_client, db, shopee_env):
    html = logged_client.get("/integracoes").text
    assert "Conectar a Shopee" in html
    r = logged_client.get("/integracoes/shopee/conectar", follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    route = respx.post(f"{HOST}/api/v2/auth/token/get").mock(return_value=_token_response())
    r = logged_client.get(f"/integracoes/shopee/retorno?code=abc&shop_id=54804&state={state}")
    assert "Shopee conectada" in r.text
    call = route.calls[0].request
    assert call.url.params["partner_id"] == PARTNER_ID and len(call.url.params["sign"]) == 64
    assert json.loads(call.content) == {"code": "abc", "shop_id": 54804, "partner_id": int(PARTNER_ID)}
    account = db.scalar(select(IntegrationAccount).where(IntegrationAccount.provider == Provider.SHOPEE,
                                                         IntegrationAccount.is_demo.is_(False)))
    assert account.external_id == "54804" and read_access_token(account) == "acesso-1"
    assert account.access_token_enc != "acesso-1"  # guardado criptografado
    assert "Loja Shopee nº 54804" in logged_client.get("/integracoes").text


@respx.mock
def test_conta_principal_usa_a_primeira_loja(shopee_env):
    route = respx.post(f"{HOST}/api/v2/auth/token/get").mock(return_value=_token_response(shop_id_list=[33142]))
    tokens, shop = auth.exchange_code("abc", main_account_id=10208)
    assert shop == 33142 and json.loads(route.calls[0].request.content)["main_account_id"] == 10208


@respx.mock
def test_erros_da_troca_do_codigo(logged_client, shopee_env):
    respx.post(f"{HOST}/api/v2/auth/token/get").mock(return_value=httpx.Response(
        200, json={"error": "invalid_code", "message": "Invalid code", "request_id": "x"}))
    with pytest.raises(IntegrationError, match="10 minutos"):
        auth.exchange_code("velho", shop_id=1)
    r = logged_client.get("/integracoes/shopee/conectar", follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    assert "10 minutos" in logged_client.get(f"/integracoes/shopee/retorno?code=x&shop_id=1&state={state}").text


def test_retorno_com_state_errado(logged_client, shopee_env):
    logged_client.get("/integracoes/shopee/conectar", follow_redirects=False)
    assert "Não foi possível confirmar" in logged_client.get(
        "/integracoes/shopee/retorno?code=x&shop_id=1&state=forjado").text


def test_sem_chaves_mostra_o_passo_a_passo(logged_client):
    html = logged_client.get("/integracoes").text
    assert "SHOPEE_PARTNER_ID" in html
    r = logged_client.get("/integracoes/shopee/conectar")
    assert "Faltam o Partner ID" in r.text


@respx.mock
def test_renovacao_guarda_o_refresh_novo(db, shopee_env):
    account = connect_shopee(db, expires_in=60)  # vence em 1 minuto: precisa renovar
    route = respx.post(f"{HOST}/api/v2/auth/access_token/get").mock(
        return_value=_token_response("acesso-2", "renova-2", shop_id=54804))
    assert keep_shopee_alive(get_sessionmaker()) is True
    body = json.loads(route.calls[0].request.content)
    assert body == {"refresh_token": "renova-0", "shop_id": 54804, "partner_id": int(PARTNER_ID)}
    db.expire_all()
    assert read_access_token(account) == "acesso-2" and read_refresh_token(account) == "renova-2"
    assert account.expires_at > utcnow() + timedelta(hours=3)


@respx.mock
def test_renovacao_recusada_pede_conectar_de_novo(db, shopee_env):
    account = connect_shopee(db, expires_in=60)
    respx.post(f"{HOST}/api/v2/auth/access_token/get").mock(return_value=httpx.Response(
        200, json={"error": "error_auth", "message": "Invalid refresh_token", "request_id": "x"}))
    assert keep_shopee_alive(get_sessionmaker()) is False
    db.expire_all()
    assert account.status == IntegrationStatus.ERROR and "Conectar de novo" in account.last_error
    with pytest.raises(AuthExpired):
        auth.refresh_tokens("x", 1)


@respx.mock
def test_shopee_instavel_nao_desconecta(db, shopee_env):
    account = connect_shopee(db, expires_in=60)
    respx.post(f"{HOST}/api/v2/auth/access_token/get").mock(return_value=httpx.Response(503))
    assert keep_shopee_alive(get_sessionmaker()) is False
    db.expire_all()
    assert account.status == IntegrationStatus.OK


def test_desconectar(logged_client, db, shopee_env):
    account = connect_shopee(db)
    token = csrf_from(logged_client.get("/integracoes").text)
    assert logged_client.post("/integracoes/shopee/desconectar", data={}).status_code == 403
    logged_client.post("/integracoes/shopee/desconectar", data={"csrf_token": token})
    db.expire_all()
    assert account.access_token_enc is None and account.status == IntegrationStatus.DISCONNECTED


def test_sem_conta_nao_renova(db, shopee_env):
    assert keep_shopee_alive(get_sessionmaker()) is False
