"""Precificação — calculadora (taxas por faixa, tarifa oficial do ML, desconto, afiliados, histórico)."""

import httpx
import respx
from sqlalchemy import select

from app.core.db import utcnow
from app.models import Listing, PriceReference, PricingSimulation, Product
from app.models.enums import Channel as Mkt
from app.services.marketplace_fees import DEFAULT_FEES
from app.services.pricing_calc import (
    Channel,
    Costs,
    band_fee,
    break_even,
    evaluate,
    evaluate_listed,
    flat_fee,
    parse_money,
    parse_percent,
    price_for,
    round_up_price,
)
from tests.conftest import csrf_from
from tests.test_ml import connect_ml, ml_env  # noqa: F401 - fixture

COSTS = Costs(cost_cents=3460, tax_pct=2.5)
ML = Channel("ML", flat_fee(12), shipping_cents=915)
SHOPEE = Channel("Shopee", band_fee(DEFAULT_FEES["shopee"]["bands"]))


def test_preco_para_margem_entrega_a_margem_e_bate_com_dado_real():
    r = price_for(COSTS, ML, margin_pct=15)
    assert r is not None and 15 <= r.margin_pct < 15.1
    assert abs(evaluate(COSTS, ML, 6162).profit_cents - 894) <= 2  # diagnóstico real de 11/11


def test_faixas_de_preco_da_shopee():
    # R$ 39,99 de custo e 20% de margem (como no print da NorteUp): cai em que faixa?
    costs = Costs(cost_cents=3999, tax_pct=2.5)
    r = price_for(costs, SHOPEE, margin_pct=20)
    assert r.margin_pct >= 20
    pct, fixed = SHOPEE.fee(r.price_cents)
    assert (r.commission_pct, r.fixed_fee_cents) == (pct, fixed)  # a taxa usada é a da faixa do preço final
    assert SHOPEE.fee(7999) == (20, 400) and SHOPEE.fee(8000) == (14, 1600) and SHOPEE.fee(25000) == (14, 2600)


def test_desconto_afiliados_e_final_90():
    costs = Costs(cost_cents=3999, tax_pct=2.5, affiliate_pct=5, discount_pct=10)
    r = price_for(costs, SHOPEE, margin_pct=20, ending_90=True)
    assert r.listed_cents % 100 == 90 and r.price_cents == round(r.listed_cents * 0.9)
    assert r.margin_pct >= 20 and r.affiliate_cents == round(r.price_cents * 0.05)
    again = evaluate_listed(costs, SHOPEE, r.listed_cents)
    assert again.profit_cents == r.profit_cents


def test_lucro_desejado_ponto_de_equilibrio_e_impossivel():
    r = price_for(COSTS, ML, profit_cents=1000)
    assert r.profit_cents >= 1000
    floor = break_even(COSTS, ML)
    assert evaluate(COSTS, ML, floor).profit_cents >= 0 and evaluate(COSTS, ML, floor - 3).profit_cents < 0
    assert price_for(Costs(1000, tax_pct=50), Channel("X", flat_fee(40)), margin_pct=20) is None


def test_leitura_de_valores():
    assert round_up_price(6123, True) == 6190 and round_up_price(6195, True) == 6290
    assert parse_money("1.234,50") == 123450 and parse_money("R$ 10") == 1000 and parse_money("x") is None
    assert parse_percent("16,5") == 16.5 and parse_percent("") is None


def _calc_url(extra=""):
    return ("/precificacao?calcular=1&nome=Pastilha&custo=39,99&rateio=0&rateio_tipo=pct&modo=margem&margem=20"
            "&regime=simples&imposto=2,5&ads=0&desconto=0&afiliados=0&final90=1&mk=shopee&mk=tiktok" + extra)


def test_pagina_calcula_compara_e_guarda_historico(logged_client, db):
    html = logged_client.get(_calc_url()).text
    assert "Shopee" in html and "TikTok Shop" in html and "Mais lucro" in html and "Ver a conta" in html
    assert "tabela de taxas do painel" in html and "style=" not in html
    # Simulador: outro preço para a Shopee.
    html = logged_client.get(_calc_url("&sim_shopee=79,90")).text
    assert 'value="79,90"' in html and "Lucro" in html
    sims = list(db.scalars(select(PricingSimulation)))
    assert len(sims) == 1 and sims[0].title == "Pastilha"  # testar preço não duplica o histórico
    assert "Pastilha" in logged_client.get("/precificacao").text  # aparece em "Últimas 10 simulações"
    for i in range(12):
        logged_client.get(_calc_url(f"&nome=P{i}"))
    assert db.query(PricingSimulation).count() == 10
    assert "Informe o custo" in logged_client.get("/precificacao?calcular=1&custo=&modo=margem&margem=20&mk=shopee").text
    assert "Marque pelo menos um" in logged_client.get("/precificacao?calcular=1&custo=10&modo=margem&margem=20").text


@respx.mock
def test_mercado_livre_usa_a_tarifa_oficial(logged_client, db, ml_env):
    connect_ml(db)
    p = Product(sku="CAN-01", name="Caneca", olist_id="o1", cost_cents=3460)
    db.add(p)
    db.flush()
    db.add(Listing(channel=Mkt.ML, external_id="MLB1", item_id="MLB1", sku="CAN-01", product_id=p.id,
                   title="Caneca", price_cents=6800, status="active", synced_at=utcnow()))
    db.add(PriceReference(channel=Mkt.ML, item_id="MLB1", product_id=p.id, current_cents=6800, suggested_cents=6162,
                          selling_fee_cents=739, shipping_fee_cents=915, checked_at=utcnow()))
    db.commit()
    base = "https://api.mercadolibre.com"
    respx.get(f"{base}/items/MLB1").mock(return_value=httpx.Response(200, json={"id": "MLB1", "category_id": "MLB1234"}))
    fees = respx.get(f"{base}/sites/MLB/listing_prices").mock(side_effect=lambda r: httpx.Response(200, json=[{
        "listing_type_id": r.url.params["listing_type_id"], "sale_fee_amount": 0,
        "sale_fee_details": {"percentage_fee": 11.5 if r.url.params["listing_type_id"] == "gold_special" else 16.5,
                             "fixed_fee": 0}}]))
    html = logged_client.get(f"/precificacao?produto={p.id}").text
    assert 'value="34,60"' in html and 'value="9,15"' in html  # custo do Olist e frete informado pelo ML
    html = logged_client.get(f"/precificacao?calcular=1&produto={p.id}&custo=34,60&modo=margem&margem=15&imposto=2,5"
                             "&ads=0&desconto=0&afiliados=0&rateio=0&rateio_tipo=pct&frete_ml=9,15"
                             "&mk=ml_classico&mk=ml_premium").text
    assert "tarifa oficial do Mercado Livre, da categoria do anúncio" in html
    assert "Comissão (11,50%)" in html and "Comissão (16,50%)" in html
    params = fees.calls[0].request.url.params
    assert params["category_id"] == "MLB1234" and params["currency_id"] == "BRL"
    assert all(c.request.method == "GET" for c in fees.calls)


def test_sem_mercado_livre_conectado_usa_estimativa(logged_client, db):
    html = logged_client.get(_calc_url("&mk=ml_classico")).text
    assert "estimativa (12%)" in html


def test_tabela_de_taxas_editavel(logged_client, db):
    html = logged_client.get("/precificacao/taxas").text
    assert "Fontes consultadas" in html and "Shopee" in html
    token = csrf_from(html)
    data = {"csrf_token": token, "shopee_max0": "79,99", "shopee_pct0": "20", "shopee_fixed0": "4,50",
            "shopee_max1": "", "shopee_pct1": "14", "shopee_fixed1": "20", "shopee_return": "0,49",
            "tiktok_max0": "", "tiktok_pct0": "6", "tiktok_fixed0": "6", "tiktok_return": "0",
            "ml_classico_fallback": "13", "ml_premium_fallback": "18"}
    r = logged_client.post("/precificacao/taxas", data=data)
    assert "Tabela de taxas salva" in r.text
    from app.services.marketplace_fees import load_fees
    db.expire_all()
    fees = load_fees(db)
    assert fees["shopee"]["bands"][0] == {"max": 7999, "pct": 20.0, "fixed": 450}
    assert fees["ml_classico"]["fallback_pct"] == 13.0
    bad = dict(data, shopee_max1="150")  # nenhuma faixa "sem limite"
    assert "deixe só a última sem valor máximo" in logged_client.post("/precificacao/taxas", data=bad).text
    assert logged_client.post("/precificacao/taxas", data={}).status_code == 403
