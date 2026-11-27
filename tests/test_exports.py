"""Relatórios para baixar: período, contas do lucro, planilha para o Excel e versão para imprimir."""

from datetime import date, datetime

from app.models import Order, OrderItem, Product
from app.models.enums import Channel
from app.services.exports import build, parse_period, to_csv
from tests.conftest import csrf_from  # noqa: F401


def test_periodos():
    today = date(2026, 11, 27)
    assert (parse_period("mes", today=today).start, parse_period("mes", today=today).end) == (date(2026, 11, 1), today)
    last = parse_period("mes_passado", today=today)
    assert (last.start, last.end) == (date(2026, 10, 1), date(2026, 10, 31))
    assert parse_period("7", today=today).start == date(2026, 11, 21)
    custom = parse_period("personalizado", "2026-11-10", "2026-11-05", today=today)
    assert (custom.start, custom.end) == (date(2026, 11, 5), date(2026, 11, 10))  # datas invertidas: ajusta
    assert parse_period("personalizado", "lixo", "", today=today).start == date(2026, 11, 1)
    assert (parse_period("personalizado", "2020-01-01", "2026-11-27", today=today).end
            - parse_period("personalizado", "2020-01-01", "2026-11-27", today=today).start).days == 399


def _sale(db, ref, channel, when, total, fee, cost_cents, qty=1, sku="PAST-01"):
    product = db.query(Product).filter_by(sku=sku).first()
    if product is None:
        product = Product(sku=sku, name="Pastilha CG 160", cost_cents=cost_cents)
        db.add(product)
        db.flush()
    o = Order(channel=channel, external_id=ref, customer_short_name="Ana S.", items_total_cents=total,
              marketplace_fee_cents=fee, fees_known=True, ordered_at=when)
    o.items.append(OrderItem(sku=sku, title="Pastilha CG 160", quantity=qty, unit_price_cents=total // qty,
                             product_id=product.id))
    db.add(o)
    db.commit()


def test_vendas_produtos_e_resumo_diario(db):
    from app.models import IntegrationAccount
    db.add(IntegrationAccount(provider="olist", account_name="Olist", is_demo=False, access_token_enc="x", extra={}))
    _sale(db, "A1", Channel.SHOPEE, datetime(2026, 11, 20, 15), 10000, 2000, 5000)
    _sale(db, "A2", Channel.SHOPEE, datetime(2026, 11, 21, 15), 20000, 4000, 5000, qty=2)
    period = parse_period("personalizado", "2026-11-20", "2026-11-21")
    sales = build(db, "vendas", period, 10.0)
    # A1: 100 − 20 taxa − 50 custo − 10 imposto = 20 de lucro
    assert sales.rows[0][2] == "A1" and sales.rows[0][10] == 2000 and round(sales.rows[0][11], 1) == 20.0
    assert sales.totals[5] == 30000 and sales.totals[10] == 2000 + 4000  # A2: 200 − 40 − 100 − 20 = 40
    products = build(db, "produtos", period, 10.0)
    assert products.rows[0][:5] == ["PAST-01", "Pastilha CG 160", 3, 30000, 6000]
    daily = build(db, "diario", period, 10.0)
    assert [r[0] for r in daily.rows] == ["20/11/2026", "21/11/2026"] and daily.rows[1][6] == 20000  # coluna Shopee
    csv = to_csv(sales)
    assert csv.startswith("﻿") and ";100,00;20,00;" in csv and "R$" not in csv


def test_tela_baixar_planilha_e_imprimir(logged_client, db):
    page = logged_client.get("/relatorios")
    assert "Relatórios para baixar" in page.text and "style=" not in page.text.split("<iframe")[0]
    r = logged_client.get("/relatorios/baixar?tipo=diario&periodo=7&formato=planilha")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and "Resumo por dia" in r.text
    p = logged_client.get("/relatorios/baixar?tipo=vendas&periodo=mes&formato=imprimir")
    assert p.status_code == 200 and "data-imprimir" in p.text and "Vendas do período" in p.text


def test_quem_nao_ve_financeiro_nao_baixa(client, db, user):
    from tests.conftest import login
    from tests.test_permissions import _add
    _add(db, "op@loja.com", "operador")
    login(client, "op@loja.com")
    r = client.get("/relatorios/baixar?tipo=vendas", follow_redirects=False)
    assert r.status_code in (303, 403)
