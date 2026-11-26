"""Precificação — contas de preço (usadas pelos preços em massa), tela da calculadora e tabela de taxas."""

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
from app.services.store_settings import set_setting
from tests.conftest import csrf_from

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


def test_tela_da_calculadora_saia_do_prejuizo(logged_client, db):
    """A calculadora roda no navegador (calculadora.js); a página traz os campos e os valores iniciais."""
    html = logged_client.get("/precificacao").text
    for field in ("custo", "tarifa", "fixa", "ads", "imposto", "outros", "margem", "markup", "atual", "desconto"):
        assert f'id="{field}"' in html
    assert "Descobrir preço de venda" in html and "Descobrir minha margem" in html and "Detalhamento" in html
    assert "/static/js/calculadora.js" in html and "style=" not in html and "<script>" not in html
    set_setting(db, "tax_rate_pct", 14.25)
    db.commit()
    assert 'id="imposto" inputmode="decimal" value="14,25"' in logged_client.get("/precificacao").text
    assert logged_client.get("/static/js/calculadora.js").status_code == 200


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
