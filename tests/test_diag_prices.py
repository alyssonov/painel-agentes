"""Diagnóstico de preços (referências de preço do Mercado Livre; só leitura)."""

import httpx
import respx

from app.cli import cmd_diag_precos
from tests import ml_fake
from tests.test_ml import connect_ml, ml_env  # noqa: F401 - fixture

DETAIL = {"item_id": "MLB1", "status": "with_benchmark_high", "current_price": {"amount": 99.9},
          "suggested_price": {"amount": 89.9}, "lowest_price": {"amount": 85.0}, "internal_price": {"amount": 90},
          "costs": {"selling_fees": 12.5, "shipping_fees": 20.1}, "applicable_suggestion": True,
          "percent_difference": 11, "metadata": {"compared_values": 7}}


@respx.mock
def test_diagnostico_de_precos(db, ml_env, capsys):
    connect_ml(db)
    ml_fake.install(respx.mock)
    base = "https://api.mercadolibre.com"
    respx.get(f"{base}/suggestions/user/123456/items").mock(return_value=httpx.Response(
        200, json={"total": 1, "items": ["MLB1"]}))
    respx.get(f"{base}/suggestions/items/MLB1/details").mock(return_value=httpx.Response(200, json=DETAIL))
    auto = respx.get(f"{base}/pricing-automation/users/123456/items").mock(return_value=httpx.Response(
        200, json={"items": [{"item_id": "MLB9", "status": "ACTIVE"}], "paging": {"total": 1}}))
    assert cmd_diag_precos(None) == 0
    out = capsys.readouterr().out
    assert "1 anúncio(s) com referência de preço" in out and "with_benchmark_high" in out
    assert "sugerido=89.9" in out and "'selling_fees': 12.5" in out and "comparou 7" in out
    assert "1 anúncio(s) com automatização" in out
    assert all(c.request.method == "GET" for c in auto.calls)


@respx.mock
def test_diagnostico_segue_mesmo_com_resposta_estranha(db, ml_env, capsys):
    connect_ml(db)
    ml_fake.install(respx.mock)
    base = "https://api.mercadolibre.com"
    respx.get(f"{base}/suggestions/user/123456/items").mock(return_value=httpx.Response(
        200, json={"total": 2, "items": ["MLB1", "MLB2"]}))
    respx.get(f"{base}/suggestions/items/MLB1/details").mock(return_value=httpx.Response(200, json=["?"]))
    respx.get(f"{base}/suggestions/items/MLB2/details").mock(return_value=httpx.Response(403, json={}))
    respx.get(f"{base}/pricing-automation/users/123456/items").mock(return_value=httpx.Response(200, json={}))
    assert cmd_diag_precos(None) == 0
    out = capsys.readouterr().out
    assert "formato inesperado" in out and "MLB2: ERRO" in out and "Automatização" in out
