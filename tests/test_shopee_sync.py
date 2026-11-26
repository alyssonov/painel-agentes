"""Shopee → painel: pedidos (situação e prazo de envio), taxas e valor líquido. API simulada."""

from datetime import datetime

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.integrations.shopee.client import ShopeeClient, to_timestamp
from app.integrations.shopee.mapping import income_from, shipment_from
from app.integrations.tokens import TokenSet, store_tokens
from app.models import IntegrationAccount, MarketplaceFee, Order, Shipment, SyncRun
from app.models.enums import Channel, KanbanStatus, Provider, SyncStatus
from app.services import dashboard
from app.sync.shopee_sync import run_shopee_sync
from tests.test_shopee_auth import shopee_env  # noqa: F401 - fixture
from tests.test_shopee_client import FakeTokens

HOST = "https://partner.shopeemobile.com/api/v2"
DEADLINE = datetime(2026, 11, 27, 18, 0)

ESCROW = {"order_sn": "SN1", "order_income": {
    "escrow_amount": 78.4, "commission_fee": 14.0, "net_commission_fee": 12.0, "service_fee": 6.0,
    "net_service_fee": 5.0, "seller_transaction_fee": 1.5, "seller_order_processing_fee": 3.0,
    "order_ams_commission_fee": 0.1, "final_shipping_fee": -4.0, "buyer_total_amount": 100.0}}


def _detail(sn, status, **extra):
    return {"order_sn": sn, "order_status": status, "ship_by_date": to_timestamp(DEADLINE),
            "package_list": [{"package_number": f"PK{sn}"}], **extra}


def _shopee_api(router, details, escrow=ESCROW):
    def ok(body):
        return httpx.Response(200, json={"error": "", "message": "", **body})
    router.get(f"{HOST}/shop/get_shop_info").mock(return_value=ok(
        {"shop_name": "Atacadão Motopeças", "status": "NORMAL", "expire_time": 1795800000}))
    router.get(f"{HOST}/order/get_order_list").mock(return_value=ok({"response": {
        "more": False, "next_cursor": "", "order_list": [{"order_sn": d["order_sn"]} for d in details]}}))
    detail_route = router.get(f"{HOST}/order/get_order_detail").mock(
        return_value=ok({"response": {"order_list": details}}))
    escrow_route = router.get(f"{HOST}/payment/get_escrow_detail").mock(
        side_effect=lambda r: ok({"response": {**escrow, "order_sn": r.url.params["order_sn"]}}))
    return detail_route, escrow_route


@pytest.fixture
def shopee_account(db, shopee_env):  # noqa: F811
    account = IntegrationAccount(provider=Provider.SHOPEE, account_name="Shopee", external_id="54804", extra={})
    store_tokens(account, TokenSet(access_token="a", refresh_token="r", expires_in=14400))
    db.add(account)
    db.commit()
    return account


def _factory(shop):
    return ShopeeClient(FakeTokens(), shop, sleep=lambda _s: None)


def test_traducao_da_situacao_e_das_taxas():
    s = shipment_from(_detail("A", "PROCESSED"))
    assert (s.status, s.substatus, s.shipment_id, s.dispatch_deadline) == ("ready_to_ship", "printed", "PKA", DEADLINE)
    assert shipment_from(_detail("B", "COMPLETED", pickup_done_time=to_timestamp(DEADLINE))).shipped_at == DEADLINE
    assert shipment_from(_detail("C", "READY_TO_SHIP", fulfillment_flag="fulfilled_by_shopee")).logistic_type \
        == "fulfillment"
    income = income_from(ESCROW)
    # taxa = comissão líquida 12 + serviço líquido 5 + transação 1,50 + por pedido 3 + afiliados 0,10
    assert (income.fee_cents, income.net_cents, income.seller_shipping_cents) == (2160, 7840, 400)
    assert income_from({}).fee_cents == 0 and income_from({}).net_cents is None


@respx.mock
def test_sincronizacao_le_envios_e_taxas_e_liga_ao_pedido_do_olist(db, shopee_account):
    db.add(Order(channel=Channel.SHOPEE, external_id="SN1", customer_short_name="Ana S.",
                 items_total_cents=10000, ordered_at=datetime(2026, 11, 26, 12, 0)))
    db.commit()
    details = [_detail("SN1", "READY_TO_SHIP"), _detail("SN2", "UNPAID"), _detail("SN3", "CANCELLED")]
    detail_route, escrow_route = _shopee_api(respx, details)
    run = run_shopee_sync(get_sessionmaker(), client_factory=_factory)
    assert run.status == SyncStatus.OK, run.error
    assert run.counts == {"pedidos": 3, "envios": 3, "taxas": 1}
    # só os campos necessários: nada de endereço/telefone do comprador
    fields = detail_route.calls[0].request.url.params["response_optional_fields"]
    assert "recipient_address" not in fields and "buyer_username" not in fields
    # taxa só de pedido pago e não cancelado
    assert [c.request.url.params["order_sn"] for c in escrow_route.calls] == ["SN1"]
    db.expire_all()
    order = db.scalar(select(Order).where(Order.external_id == "SN1"))
    assert (order.marketplace_fee_cents, order.fees_known, order.net_received_cents) == (2160, True, 7840)
    ship = db.scalar(select(Shipment).where(Shipment.order_ref == "SN1"))
    assert (ship.channel, ship.status, ship.dispatch_deadline) == (Channel.SHOPEE, "ready_to_ship", DEADLINE)
    account = db.get(IntegrationAccount, shopee_account.id)
    assert account.account_name == "Atacadão Motopeças" and account.extra["shop_status"] == "NORMAL"
    # 2ª rodada: começa do cursor (com folga), não 15 dias atrás
    run_shopee_sync(get_sessionmaker(), client_factory=_factory)
    first, second = [c.request.url.params for c in respx.calls if "get_order_list" in str(c.request.url)][:2]
    assert int(second["time_from"]) > int(first["time_from"])
    assert all(c.request.method == "GET" for c in respx.calls)


@respx.mock
def test_taxa_lida_antes_do_pedido_chegar_no_olist_e_ligada_depois(db, shopee_account):
    _shopee_api(respx, [_detail("SN9", "SHIPPED")])
    run_shopee_sync(get_sessionmaker(), client_factory=_factory)
    fee = db.scalar(select(MarketplaceFee).where(MarketplaceFee.order_ref == "SN9"))
    assert (fee.channel, fee.fee_cents, fee.net_cents) == (Channel.SHOPEE, 2160, 7840)


@respx.mock
def test_quadro_envios_de_hoje_mostra_a_shopee(db, shopee_account, monkeypatch):
    from app.core import timeutil
    db.add(Order(channel=Channel.SHOPEE, external_id="SN1", customer_short_name="Ana S.",
                 items_total_cents=10000, ordered_at=datetime(2026, 11, 26, 12, 0),
                 kanban_status=KanbanStatus.TO_PICK))
    db.commit()
    _shopee_api(respx, [_detail("SN1", "PROCESSED")])
    run_shopee_sync(get_sessionmaker(), client_factory=_factory)
    monkeypatch.setattr(dashboard, "local_today", lambda: DEADLINE.date())
    monkeypatch.setattr(dashboard, "utcnow", lambda: datetime(2026, 11, 27, 12, 0))
    columns = {c.status: c for c in dashboard._shipment_cards(db, Channel.SHOPEE)[0]}
    cards = columns[KanbanStatus.PACKED].orders
    assert [c.external_id for c in cards] == ["SN1"] and cards[0].channel == Channel.SHOPEE
    assert dashboard._shipment_cards(db, Channel.ML)[0][0].orders == []


@respx.mock
def test_erro_de_acesso_vira_alerta_sem_derrubar(db, shopee_account):
    respx.get(f"{HOST}/shop/get_shop_info").mock(
        return_value=httpx.Response(403, json={"error": "error_auth", "message": "Invalid access_token."}))
    run = run_shopee_sync(get_sessionmaker(), client_factory=_factory)
    assert run.status == SyncStatus.ERROR and "Conectar de novo" in run.error


def test_sem_conta_nao_faz_nada(db):
    assert run_shopee_sync(get_sessionmaker()) is None
    assert db.scalar(select(SyncRun)) is None
