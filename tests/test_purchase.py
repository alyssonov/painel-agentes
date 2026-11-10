"""Sugestão de compra (Estoque e preços)."""

from app.services.purchase import purchase_suggestions
from app.services.stock import stock_rows
from tests.olist_helpers import connect_account
from tests.test_phase4 import product, sale


def test_conta_da_sugestao_de_compra(db):
    # 30 vendidos em 30 dias = 1 por dia.
    fast = product(db, "RAPIDO", cost=1500, stock=5)   # dura 5 dias < prazo de 10 → comprar já
    soon = product(db, "LOGO", cost=1000, stock=14)    # dura 14 dias ≤ 10 + 7 → esta semana
    calm = product(db, "TRANQUILO", stock=100)         # dura 100 dias → nada
    still = product(db, "PARADO", stock=0)             # sem vendas → nada
    for prod in (fast, soon, calm):
        sale(db, prod, qty=30, days_ago=3)
    db.commit()
    items = purchase_suggestions(db, stock_rows(db, window_days=30), lead_days=10, coverage_days=30)
    assert [(i.sku, i.urgency, i.quantity) for i in items] == [("RAPIDO", "agora", 35), ("LOGO", "semana", 26)]
    assert items[0].total_cost_cents == 35 * 1500
    assert still.sku not in {i.sku for i in items}


def test_esgotado_que_vendia_entra_como_urgente(db):
    prod = product(db, "ZERO", stock=-2)  # reservado maior que o saldo conta como zero
    sale(db, prod, qty=60, days_ago=2)
    db.commit()
    (item,) = purchase_suggestions(db, stock_rows(db, window_days=30), lead_days=10, coverage_days=30)
    assert item.available == 0 and item.quantity == 80 and item.urgency == "agora"


def test_pagina_e_planilha(logged_client, db):
    connect_account(db)
    prod = product(db, "CAN-01", cost=2500, stock=3)
    sale(db, prod, qty=30, days_ago=1)
    db.commit()
    html = logged_client.get("/estoque-precos").text
    assert "Sugestão de compra" in html and "Comprar já" in html and "37 un." in html and "R$ 925,00" in html
    response = logged_client.get("/estoque-precos/sugestao-de-compra.csv")
    assert response.status_code == 200 and "attachment" in response.headers["content-disposition"]
    text = response.content.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("SKU;Produto;")
    assert "CAN-01;Produto CAN-01;3;1,0;3;37;R$ 25,00;R$ 925,00;Comprar já" in text


def test_planilha_exige_login(client):
    assert client.get("/estoque-precos/sugestao-de-compra.csv", follow_redirects=False).status_code in (303, 401)
