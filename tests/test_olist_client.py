"""Cliente do Olist: somente leitura, paginação, limites e novas tentativas."""

import ast
from pathlib import Path

import httpx
import pytest
import respx

from app.integrations.errors import AuthExpired, IntegrationError, TemporaryError
from app.integrations.olist.client import BASE_URL, OlistClient
from app.integrations.ratelimit import HeaderRateLimiter
from tests import olist_fake


class FakeTokens:
    def __init__(self):
        self.forced = 0

    def get_token(self, *, force_refresh=False):
        if force_refresh:
            self.forced += 1
            return "token-novo"
        return "token"


def make_client(sleeps=None):
    return OlistClient(FakeTokens(), sleep=(sleeps.append if sleeps is not None else (lambda _s: None)))


# --- Regra do projeto: o Olist é somente leitura ------------------------------

WRITE_METHODS = {"post", "put", "patch", "delete", "request", "stream", "send"}


def _http_calls(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}


def test_cliente_do_olist_nao_tem_nenhuma_escrita():
    calls = _http_calls(Path("app/integrations/olist/client.py"))
    assert not calls & WRITE_METHODS, f"O cliente do Olist só pode fazer GET: {calls & WRITE_METHODS}"


def test_sincronizacao_nao_chama_http_diretamente():
    calls = _http_calls(Path("app/sync/olist_sync.py"))
    assert not calls & WRITE_METHODS


def test_unico_post_e_o_de_login_no_servidor_de_autorizacao():
    # O único POST permitido é a troca/renovação de token em accounts.tiny.com.br.
    source = Path("app/integrations/olist/auth.py").read_text(encoding="utf-8")
    assert source.count(".post(") == 1
    assert "client.post(TOKEN_URL" in source


def test_metodos_publicos_sao_so_de_leitura():
    public = [n for n in dir(OlistClient) if not n.startswith("_")]
    assert all(n.startswith(("get_", "iter_")) for n in public), public


# --- Comportamento -----------------------------------------------------------

@respx.mock
def test_paginacao_percorre_todas_as_paginas(monkeypatch):
    monkeypatch.setattr("app.integrations.olist.client.PAGE_SIZE", 2)
    olist_fake.install(respx.mock)
    with make_client() as client:
        produtos = list(client.iter_products())
    assert [p["id"] for p in produtos] == [101, 102, 103]


@respx.mock
def test_limite_estourado_espera_o_reset_e_tenta_de_novo():
    route = respx.get(f"{BASE_URL}/info").mock(side_effect=[
        httpx.Response(429, headers={"X-RateLimit-Reset": "7"}),
        httpx.Response(200, json={"fantasia": "Loja"}),
    ])
    sleeps = []
    with make_client(sleeps) as client:
        assert client.get_company_info() == {"fantasia": "Loja"}
    assert route.call_count == 2
    assert sleeps == [7.0]


@respx.mock
def test_erro_de_servidor_tenta_algumas_vezes_e_desiste():
    route = respx.get(f"{BASE_URL}/info").mock(return_value=httpx.Response(503))
    sleeps = []
    with make_client(sleeps) as client, pytest.raises(TemporaryError):
        client.get_company_info()
    assert route.call_count == 5
    assert len(sleeps) == 4 and sleeps == sorted(sleeps)  # espera crescente


@respx.mock
def test_token_recusado_renova_uma_vez():
    tokens = FakeTokens()
    seen = []

    def responder(request):
        seen.append(request.headers["Authorization"])
        return httpx.Response(401) if len(seen) == 1 else httpx.Response(200, json={"ok": 1})

    respx.get(f"{BASE_URL}/info").mock(side_effect=responder)
    with OlistClient(tokens, sleep=lambda _s: None) as client:
        assert client.get_company_info() == {"ok": 1}
    assert tokens.forced == 1
    assert seen == ["Bearer token", "Bearer token-novo"]


@respx.mock
def test_token_recusado_duas_vezes_pede_reconexao():
    respx.get(f"{BASE_URL}/info").mock(return_value=httpx.Response(401))
    with make_client() as client, pytest.raises(AuthExpired):
        client.get_company_info()


@respx.mock
def test_sem_permissao_explica_o_que_fazer():
    respx.get(f"{BASE_URL}/notas").mock(return_value=httpx.Response(403))
    with make_client() as client, pytest.raises(IntegrationError, match="permissões"):
        list(client.iter_invoices(created_since=__import__("datetime").datetime(2026, 1, 1)))


@respx.mock
def test_parametros_de_data_vao_no_formato_do_olist():
    route = respx.get(f"{BASE_URL}/pedidos").mock(
        return_value=httpx.Response(200, json={"itens": [], "paginacao": {"total": 0}}))
    from datetime import datetime
    with make_client() as client:
        list(client.iter_orders(updated_since=datetime(2026, 11, 6, 13, 0)))
    params = route.calls[0].request.url.params
    assert params["dataAtualizacao"] == "2026-11-06 10:00:00"
    assert "dataInicial" not in params


def test_limitador_espera_quando_o_minuto_acaba():
    sleeps, now = [], [100.0]
    limiter = HeaderRateLimiter(reserve=2, sleep=sleeps.append, clock=lambda: now[0])
    limiter.update({"X-RateLimit-Remaining": "5", "X-RateLimit-Reset": "20"})
    limiter.wait()
    assert sleeps == []
    limiter.update({"X-RateLimit-Remaining": "2", "X-RateLimit-Reset": "20"})
    limiter.wait()
    assert sleeps == [20.0]
    limiter.update({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "20"})
    now[0] += 30  # o minuto já virou
    limiter.wait()
    assert sleeps == [20.0]
