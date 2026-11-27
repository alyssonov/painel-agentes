"""Custo usado no lucro: Onix primeiro; sem ele, Olist (regra do dono, 27/11/2026)."""

from app.models.product_extra import ProductExtra
from app.services.costs import cost_source, unit_cost
from app.services.dashboard import order_margin
from tests.test_phase4 import product, sale


def test_onix_vale_mais_que_o_olist(db):
    com_onix, so_olist, sem_nada = product(db, "A", cost=2000), product(db, "B", cost=1500), product(db, "C", cost=0)
    db.add(ProductExtra(product_id=com_onix.id, onix_cost_cents=2470, compatibility=[]))
    db.commit()
    assert (unit_cost(com_onix), cost_source(com_onix)) == (2470, "Onix")
    assert (unit_cost(so_olist), cost_source(so_olist)) == (1500, "Olist")
    assert (unit_cost(sem_nada), cost_source(sem_nada)) == (0, None)
    order = sale(db, com_onix, qty=2, price=10000, channel="shopee")
    db.commit()
    assert order_margin(order, 0).cost_cents == 2 * 2470
