"""Venda ainda não conferida: estima o que o marketplace fica (em vez de taxa zero)."""

from app.models.enums import Channel
from app.services.dashboard import FALLBACK_RATE, order_margin
from tests.test_phase4 import product, sale


def test_sem_vendas_conferidas_usa_a_estimativa_padrao(db):
    prod = product(db, "A", cost=1000, price=10000)
    order = sale(db, prod, channel=Channel.SHOPEE, fees_known=False)
    db.commit()
    m = order_margin(order, 0)
    assert m.estimated_cents == round(10000 * FALLBACK_RATE) and m.fee_cents + m.shipping_cents == 2500
    assert m.profit_cents == 10000 - 2500 - 1000


def test_usa_o_percentual_real_das_vendas_conferidas_do_mesmo_marketplace(db):
    prod = product(db, "A", cost=1000, price=10000)
    for i in range(10):
        o = sale(db, prod, days_ago=i + 2)
        o.net_received_cents = 7000  # ML ficou 30%
    pending = sale(db, prod, days_ago=1, fee=1200)  # só a comissão conhecida
    db.commit()
    m = order_margin(pending, 0)
    assert m.fee_cents + m.shipping_cents == 3000 and m.estimated_cents == 1800


def test_venda_conferida_nao_e_estimada(db):
    prod = product(db, "A", cost=1000, price=10000)
    order = sale(db, prod)
    order.net_received_cents = 9500
    db.commit()
    m = order_margin(order, 0)
    assert m.estimated_cents == 0 and m.fee_cents + m.shipping_cents == 500
