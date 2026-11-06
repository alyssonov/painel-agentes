"""Autorização OAuth do Olist: endereço, troca do código e renovação."""

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from app.core.db import get_sessionmaker, utcnow
from app.integrations.errors import AuthExpired, IntegrationError, NotConnected
from app.integrations.olist import auth
from app.integrations.tokens import read_access_token, read_refresh_token
from app.models import IntegrationAccount
from app.models.enums import IntegrationStatus
from tests.olist_helpers import connect_account


def test_endereco_de_autorizacao(olist_env):
    url = urlparse(auth.authorize_url("estado123"))
    q = parse_qs(url.query)
    assert url.netloc == "accounts.tiny.com.br"
    assert q["client_id"] == ["id-de-teste"]
    assert q["redirect_uri"] == ["http://localhost:8000/integracoes/olist/retorno"]
    assert q["response_type"] == ["code"] and q["scope"] == ["openid"]
    assert q["state"] == ["estado123"]


@respx.mock
def test_troca_do_codigo_pelos_tokens(olist_env):
    route = respx.post(auth.TOKEN_URL).mock(return_value=httpx.Response(200, json={
        "access_token": "a", "refresh_token": "r", "expires_in": 14400, "refresh_expires_in": 86400}))
    tokens = auth.exchange_code("codigo-xyz")
    body = parse_qs(route.calls[0].request.content.decode())
    assert body["grant_type"] == ["authorization_code"]
    assert body["code"] == ["codigo-xyz"]
    assert body["client_secret"] == ["segredo-de-teste"]
    assert tokens.access_token == "a" and tokens.expires_in == 14400
    assert "a" not in repr(tokens)  # tokens nunca aparecem em logs


@respx.mock
def test_refresh_expirado_pede_reconexao(olist_env):
    respx.post(auth.TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(AuthExpired):
        auth.refresh_tokens("velho")


@respx.mock
def test_chaves_erradas_dizem_onde_corrigir(olist_env):
    respx.post(auth.TOKEN_URL).mock(return_value=httpx.Response(401, json={"error": "invalid_client"}))
    with pytest.raises(IntegrationError, match="OLIST_CLIENT_ID"):
        auth.exchange_code("x")


def test_sem_conta_conectada(db, olist_env):
    with pytest.raises(NotConnected):
        auth.OlistTokenProvider(get_sessionmaker()).get_token()


def test_token_valido_nao_renova(db, olist_env):
    connect_account(db)
    with respx.mock:  # qualquer chamada de rede falharia o teste
        assert auth.OlistTokenProvider(get_sessionmaker()).get_token() == "access-atual"


@respx.mock
def test_token_perto_de_expirar_e_renovado_e_guardado_criptografado(db, olist_env):
    connect_account(db, expires_in=60)  # expira em 1 minuto
    route = respx.post(auth.TOKEN_URL).mock(return_value=httpx.Response(200, json={
        "access_token": "renovado", "refresh_token": "refresh-2", "expires_in": 14400,
        "refresh_expires_in": 86400}))
    assert auth.OlistTokenProvider(get_sessionmaker()).get_token() == "renovado"
    assert parse_qs(route.calls[0].request.content.decode())["refresh_token"] == ["refresh-atual"]
    db.expire_all()
    account = db.query(IntegrationAccount).one()
    assert "renovado" not in account.access_token_enc  # criptografado no banco
    assert read_access_token(account) == "renovado"
    assert read_refresh_token(account) == "refresh-2"
    assert account.expires_at > utcnow() + timedelta(hours=3)


@respx.mock
def test_renovacao_recusada_marca_conta_com_erro(db, olist_env):
    connect_account(db, expires_in=60)
    respx.post(auth.TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(AuthExpired):
        auth.OlistTokenProvider(get_sessionmaker()).get_token()
    db.expire_all()
    assert db.query(IntegrationAccount).one().status == IntegrationStatus.ERROR
