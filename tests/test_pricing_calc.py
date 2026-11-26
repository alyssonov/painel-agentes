"""Precificação — calculadora (margem → preço, preço → margem, comparação)."""

from app.models import PriceReference, Product
from app.models.enums import Channel as Mkt
from app.core.db import utcnow
from app.services.pricing_calc import (
    Channel,
    Costs,
    break_even,
    evaluate,
    parse_money,
    parse_percent,
    price_for,
    round_up_price,
)

COSTS = Costs(cost_cents=3460, tax_pct=2.5)
ML = Channel("ML", commission_pct=12, shipping_cents=915)


def test_preco_para_margem_entrega_a_margem():
    r = price_for(COSTS, ML, margin_pct=15)
    assert r is not None and r.margin_pct >= 15 and r.margin_pct < 15.1
    # Conferência com os números reais do diagnóstico: a R$ 61,62 o lucro é ~R$ 8,94.
    real = evaluate(COSTS, ML, 6162)
    assert abs(real.profit_cents - 894) <= 2


def test_preco_para_lucro_e_ponto_de_equilibrio():
    r = price_for(COSTS, ML, profit_cents=1000)
    assert 1000 <= r.profit_cents <= 1001
    floor = break_even(COSTS, ML)
    assert 0 <= evaluate(COSTS, ML, floor).profit_cents <= 1
    assert evaluate(COSTS, ML, floor - 2).profit_cents < 0


def test_taxa_fixa_outros_custos_e_publicidade():
    costs = Costs(cost_cents=2000, tax_pct=6, ads_pct=5, other_pct=2, other_cents=150)
    shopee = Channel("Shopee", commission_pct=20, fixed_fee_cents=400)
    r = evaluate(costs, shopee, 5000)
    assert (r.commission_cents, r.fixed_fee_cents, r.tax_cents, r.ads_cents, r.other_cents) == (1000, 400, 300, 250, 250)
    assert r.received_cents == 3600 and r.profit_cents == 3600 - 300 - 250 - 250 - 2000


def test_impossivel_quando_percentuais_passam_de_100():
    assert price_for(Costs(1000, tax_pct=50), Channel("X", 40), margin_pct=20) is None


def test_arredondamento_e_leitura_de_valores():
    assert round_up_price(6123, True) == 6190 and round_up_price(6195, True) == 6290 and round_up_price(6123, False) == 6123
    assert parse_money("1.234,50") == 123450 and parse_money("R$ 10") == 1000 and parse_money("abc") is None
    assert parse_percent("16,5") == 16.5 and parse_percent("") is None


def test_pagina_calcula_e_compara(logged_client, db):
    url = ("/precificacao?calcular=1&custo=34,60&imposto=2,5&modo=margem&alvo=15"
           "&nome0=ML+Clássico&comissao0=12&frete0=9,15&nome1=Shopee&comissao1=20&taxa1=4&nome2=Vazio&comissao2=")
    html = logged_client.get(url).text
    assert "Resultado" in html and "ML Clássico" in html and "Shopee" in html and "Mais lucro" in html
    assert "Vazio</th>" not in html  # coluna sem comissão fica de fora
    assert "style=" not in html
    html = logged_client.get("/precificacao?calcular=1&custo=&modo=margem&alvo=15&comissao0=12").text
    assert "Informe o custo" in html


def test_produto_preenche_custo_e_taxas_do_mercado_livre(logged_client, db):
    p = Product(sku="CAN-01", name="Caneca Térmica", olist_id="o1", cost_cents=3460)
    db.add(p)
    db.flush()
    db.add(PriceReference(channel=Mkt.ML, item_id="MLB1", product_id=p.id, current_cents=6800, suggested_cents=6162,
                          selling_fee_cents=739, shipping_fee_cents=915, checked_at=utcnow()))
    db.commit()
    html = logged_client.get("/precificacao?busca=caneca").text
    assert f"/precificacao?produto={p.id}" in html
    html = logged_client.get(f"/precificacao?produto={p.id}").text
    assert 'value="34,60"' in html and 'value="11,99"' in html and 'value="9,15"' in html
    assert "informados pelo Mercado Livre para o anúncio MLB1" in html
    assert "Precificação" in logged_client.get("/").text  # no menu
