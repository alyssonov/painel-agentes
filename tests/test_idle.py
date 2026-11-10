"""Produtos parados (Estoque e preços)."""

from app.services.idle import idle_products
from app.services.stock import stock_rows
from tests.olist_helpers import connect_account
from tests.test_phase4 import product, sale


def test_parados_e_estoque_demais(db):
    old = product(db, "ANTIGO", cost=1000, stock=10)     # última venda há 90 dias → parado
    never = product(db, "NUNCA", cost=500, stock=4)      # nunca vendeu → parado
    slow = product(db, "LENTO", cost=200, stock=300)     # 30 em 30 dias = 1/dia → dura 300 dias → excesso
    good = product(db, "BOM", stock=20)                  # vende bem → fora
    empty = product(db, "VAZIO", stock=0)                # sem estoque → fora
    sale(db, old, qty=1, days_ago=90)
    sale(db, slow, qty=30, days_ago=2)
    sale(db, good, qty=30, days_ago=1)
    sale(db, empty, qty=5, days_ago=100)
    db.commit()
    report = idle_products(db, stock_rows(db, window_days=30), idle_days=60, excess_days=180)
    kinds = {i.sku: i.kind for i in report.items}
    assert kinds == {"ANTIGO": "parado", "NUNCA": "parado", "LENTO": "excesso"}
    assert report.items[0].sku == "LENTO" and report.items[0].tied_cents == 300 * 200  # mais dinheiro primeiro
    assert report.tied_cents == 60000 + 10000 + 2000
    assert report.history_complete  # há venda de 100 dias atrás: histórico cobre os 60 dias


def test_avisa_quando_o_historico_ainda_e_curto(logged_client, db):
    connect_account(db)
    prod = product(db, "NOVO", cost=1000, stock=5)
    sale(db, product(db, "OUTRO", stock=0), qty=1, days_ago=10)
    db.commit()
    report = idle_products(db, stock_rows(db, window_days=30), idle_days=60, excess_days=180)
    assert not report.history_complete and [i.sku for i in report.items] == [prod.sku]
    html = logged_client.get("/estoque-precos").text
    assert "Produtos parados" in html and "ainda está buscando o histórico" in html and "R$ 50,00" in html
