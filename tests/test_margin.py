"""Cálculo da margem real."""

from app.core.money import format_brl, format_pct
from app.services.margin import calculate_margin


def test_margem_basica():
    # Venda de R$ 100: taxa R$ 14, frete R$ 20, custo R$ 30, imposto 10% = R$ 10.
    r = calculate_margin(revenue_cents=10000, fee_cents=1400, shipping_cents=2000,
                         cost_cents=3000, tax_rate_pct=10)
    assert r.tax_cents == 1000
    assert r.profit_cents == 2600
    assert r.margin_pct == 26.0
    assert not r.is_loss


def test_prejuizo():
    r = calculate_margin(revenue_cents=4490, fee_cents=1298, shipping_cents=0,
                         cost_cents=3800, tax_rate_pct=12)
    assert r.is_loss
    assert r.profit_cents == 4490 - 1298 - 3800 - 539


def test_sem_receita_nao_divide_por_zero():
    r = calculate_margin(revenue_cents=0, fee_cents=0, shipping_cents=0, cost_cents=0, tax_rate_pct=10)
    assert r.margin_pct is None


def test_formatacao_brasileira():
    assert format_brl(123456) == "R$ 1.234,56"
    assert format_brl(-505) == "R$ -5,05"
    assert format_brl(7) == "R$ 0,07"
    assert format_pct(18.25) == "18,2%" or format_pct(18.25) == "18,3%"
    assert format_pct(None) == "—"
