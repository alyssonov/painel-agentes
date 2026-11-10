"""Conferir uma venda (Margem real)."""

from app.models import Payment
from app.models.enums import Channel
from app.services.store_settings import set_setting
from tests.olist_helpers import connect_account
from tests.test_phase4 import product, sale


def test_conferir_venda_com_valor_recebido(logged_client, db):
    connect_account(db)
    set_setting(db, "tax_rate_pct", 2.5)
    prod = product(db, "CAN", cost=3000, price=9490)
    order = sale(db, prod, qty=1, fee=1139)
    order.net_received_cents = 6876
    db.add(Payment(channel=Channel.ML, payment_id="1", order_ref=order.external_id, status="approved",
                   amount_cents=9490, net_cents=6876))
    db.commit()
    r = logged_client.get(f"/margem/venda?numero={order.external_id}")
    html = r.text
    assert str(r.url).endswith(f"/margem/venda/{order.id}")
    assert "Taxa e frete conferidos" in html and "Você recebe (Mercado Pago)" in html
    assert "R$ 68,76" in html and "R$ 11,39" in html and "R$ 14,75" in html  # recebido, comissão, frete+tarifas
    # Lucro: 68,76 − 30,00 − 2,37 = 36,39
    assert "R$ 36,39" in html and "style=" not in html
    # Também acha pelo número do Olist.
    assert str(logged_client.get(f"/margem/venda?numero={order.olist_number}").url).endswith(f"/{order.id}")


def test_venda_nao_conferida_e_busca_sem_resultado(logged_client, db):
    connect_account(db)
    order = sale(db, product(db, "Y", cost=0, price=1000), qty=1, fee=100)
    db.commit()
    html = logged_client.get(f"/margem/venda/{order.id}").text
    assert "Taxa e frete ainda não conferidos" in html and "sem custo no Olist" in html
    html = logged_client.get("/margem/venda?numero=999").text
    assert "Não achei a venda" in html and "Vendas recentes" in html
    assert "Conferir uma venda" in logged_client.get("/margem").text
