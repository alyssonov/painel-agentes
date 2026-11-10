"""Curva ABC (Margem real → Curva ABC)."""

from app.services.abc import abc_curve
from app.services.margin_report import ProductMargin
from tests.olist_helpers import connect_account
from tests.test_phase4 import product, sale


def pm(name, revenue, profit=0):
    return ProductMargin(name, name, name, units=1, revenue=revenue, profit=profit)


def test_classes_pela_regra_80_95():
    products = [pm("a", 7000), pm("b", 1500, 300), pm("c", 800), pm("d", 400), pm("e", 300)]
    rows, summary = abc_curve(products)
    assert [(r.product.name, r.cls) for r in rows] == [("a", "A"), ("b", "A"), ("c", "B"), ("d", "B"), ("e", "C")]
    assert rows[-1].cumulative == 100.0
    assert [(s.cls, s.products) for s in summary] == [("A", 2), ("B", 2), ("C", 1)]
    assert round(summary[0].share_value) == 85


def test_por_lucro_quem_da_prejuizo_fica_em_c():
    rows, _ = abc_curve([pm("bom", 1000, 500), pm("ruim", 5000, -200), pm("medio", 800, 100)], "lucro")
    assert [(r.product.name, r.cls) for r in rows] == [("bom", "A"), ("medio", "B"), ("ruim", "C")]


def test_sem_vendas():
    rows, summary = abc_curve([])
    assert rows == [] and all(s.products == 0 for s in summary)


def test_pagina_e_planilha(logged_client, db):
    connect_account(db)
    big = product(db, "GRANDE", cost=1000, price=10000)
    small = product(db, "PEQUENO", cost=100, price=1000)
    sale(db, big, qty=9, days_ago=5)
    sale(db, small, qty=1, days_ago=5)
    db.commit()
    html = logged_client.get("/margem/curva-abc?periodo=30").text
    assert "Classe A" in html and "Produto GRANDE" in html and "Baixar planilha" in html
    assert "style=" not in html  # CSP: sem estilo inline
    assert "Curva ABC dos produtos" in logged_client.get("/margem").text
    text = logged_client.get("/margem/curva-abc.csv?periodo=30&criterio=lucro").content.decode("utf-8-sig")
    lines = text.splitlines()
    assert lines[0].startswith("Posição;Classe;SKU") and lines[1].startswith("1;A;GRANDE;Produto GRANDE;9;")
    assert logged_client.get("/margem/curva-abc?periodo=999&criterio=x").status_code == 200
