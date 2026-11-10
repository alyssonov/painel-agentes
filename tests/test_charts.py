"""Gráficos (Margem real → Gráficos)."""

from app.services.charts import bar_chart, daily_points, money_tick, nice_step
from tests.olist_helpers import connect_account
from tests.test_phase4 import product, sale


def test_um_ponto_por_dia_inclusive_sem_venda(db):
    prod = product(db, "A", cost=1000, price=5000)
    sale(db, prod, qty=2, days_ago=1)
    db.commit()
    points = daily_points(db, 7, 0)
    assert len(points) == 7 and sum(p.orders for p in points) == 1
    assert sum(p.revenue for p in points) == 10000 and sum(p.profit for p in points) == 8000


def test_escala_redonda_e_rotulos():
    assert nice_step(9000) == 5000 and nice_step(450) == 200
    assert money_tick(500000) == "R$ 5.000" and money_tick(2000000) == "R$ 20 mil"


def test_barras_finas_e_prejuizo_para_baixo(db):
    from datetime import date

    from app.services.charts import DayPoint
    points = [DayPoint(date(2026, 11, d), revenue=r, profit=p, orders=1)
              for d, r, p in ((1, 10000, 3000), (2, 0, 0), (3, 8000, -2000))]
    chart = bar_chart(points, "profit")
    assert chart.has_negative and chart.bars[2].negative and not chart.bars[0].negative
    assert chart.bars[1].path == ""  # dia sem venda: sem barra (mas com área de dica)
    assert "prejuízo" in chart.bars[2].tip
    labels = [t.label for t in chart.ticks]
    assert labels[0].startswith("R$ -") and "R$ 0" in labels
    # Barras de no máximo 24px: a largura vem do caminho (H final − início).
    assert all(b.hit_w >= 24 for b in chart.bars)


def test_pagina_de_graficos(logged_client, db):
    connect_account(db)
    prod = product(db, "G", cost=1000, price=5000)
    sale(db, prod, qty=1, days_ago=2)
    db.commit()
    html = logged_client.get("/margem/graficos?periodo=30").text
    assert "Faturamento por dia" in html and "Lucro real por dia" in html and "<svg" in html
    assert "data-tip=" in html and "style=" not in html  # CSP: sem estilo inline
    assert "Ver os números em tabela" in html
    assert logged_client.get("/margem/graficos?periodo=abc").status_code == 200
    assert "Gráficos →" in logged_client.get("/margem").text
