"""Shopee — cliente de leitura (assinatura por chamada, lista de pedidos, erros)."""

import hashlib
import hmac
from datetime import datetime

import httpx
import pytest
import respx

from app.integrations.errors import AuthExpired, IntegrationError
from app.integrations.shopee import client as shopee_client
from app.integrations.shopee.client import ShopeeClient, from_timestamp, to_timestamp
from tests.test_shopee_auth import PARTNER_ID, PARTNER_KEY, shopee_env  # noqa: F401 - fixture

LIST = "https://partner.shopeemobile.com/api/v2/order/get_order_list"


class FakeTokens:
    def __init__(self):
        self.refreshed = 0

    def get_token(self, *, force_refresh=False):
        if force_refresh:
            self.refreshed += 1
        return f"tok{self.refreshed}"


def _client(tokens=None):
    return ShopeeClient(tokens or FakeTokens(), 54804, sleep=lambda _s: None)


def _ok(order_sns, more=False, cursor=""):
    return httpx.Response(200, json={"error": "", "message": "", "request_id": "r", "response": {
        "more": more, "next_cursor": cursor, "order_list": [{"order_sn": sn} for sn in order_sns]}})


def test_cliente_so_tem_leitura():
    public = [n for n in dir(ShopeeClient) if not n.startswith("_")]
    assert all(n.startswith(("get_", "iter_")) or n in ("label", "base_url") for n in public)


@respx.mock
def test_lista_pedidos_paginada_e_assinada(shopee_env):
    route = respx.get(LIST).mock(side_effect=[_ok(["A", "B"], more=True, cursor="20"), _ok(["C"])])
    since, until = datetime(2026, 11, 20), datetime(2026, 11, 26)
    with _client() as c:
        assert list(c.iter_order_sns(since, until)) == ["A", "B", "C"]
    first, second = (call.request.url.params for call in route.calls)
    assert first["shop_id"] == "54804" and first["partner_id"] == PARTNER_ID and first["access_token"] == "tok0"
    assert first["time_range_field"] == "update_time" and first["page_size"] == "100" and first["cursor"] == ""
    base = f"{PARTNER_ID}/api/v2/order/get_order_list{first['timestamp']}tok054804"
    assert first["sign"] == hmac.new(PARTNER_KEY.encode(), base.encode(), hashlib.sha256).hexdigest()
    assert second["cursor"] == "20"
    assert all(call.request.method == "GET" for call in route.calls)


@respx.mock
def test_periodo_maior_que_15_dias_vira_fatias(shopee_env):
    route = respx.get(LIST).mock(side_effect=lambda r: _ok([r.url.params["time_from"]]))
    with _client() as c:
        sns = list(c.iter_order_sns(datetime(2026, 10, 1), datetime(2026, 11, 26)))
    assert len(sns) == 4
    for call in route.calls:
        p = call.request.url.params
        assert int(p["time_to"]) - int(p["time_from"]) < 15 * 86400


@respx.mock
def test_token_recusado_renova_uma_vez(shopee_env):
    tokens = FakeTokens()
    respx.get(LIST).mock(side_effect=[
        httpx.Response(403, json={"error": "error_auth", "message": "Invalid access_token."}), _ok(["X"])])
    with _client(tokens) as c:
        assert list(c.iter_order_sns(datetime(2026, 11, 25), datetime(2026, 11, 26))) == ["X"]
    assert tokens.refreshed == 1
    respx.get(LIST).mock(return_value=httpx.Response(403, json={"error": "error_auth", "message": "x"}))
    with _client() as c, pytest.raises(AuthExpired):
        list(c.iter_order_sns(datetime(2026, 11, 25), datetime(2026, 11, 26)))


@respx.mock
def test_instavel_tenta_de_novo_e_permissao_explica(shopee_env, monkeypatch):
    route = respx.get(LIST).mock(side_effect=[httpx.Response(500), _ok(["Y"])])
    with _client() as c:
        assert list(c.iter_order_sns(datetime(2026, 11, 25), datetime(2026, 11, 26))) == ["Y"]
    assert route.call_count == 2
    respx.get(LIST).mock(return_value=httpx.Response(200, json={"error": "error_permission", "message": "no"}))
    with _client() as c, pytest.raises(IntegrationError, match="permissão"):
        list(c.iter_order_sns(datetime(2026, 11, 25), datetime(2026, 11, 26)))


def test_conversao_de_horarios():
    dt = datetime(2026, 11, 26, 12, 0)
    assert from_timestamp(to_timestamp(dt)) == dt and from_timestamp(0) is None and from_timestamp("x") is None
    assert shopee_client.MAX_WINDOW.days == 15
