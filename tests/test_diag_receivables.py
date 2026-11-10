"""Diagnóstico de recebimentos: Mercado Livre → Mercado Pago, e contas a receber do Olist (só leitura)."""

import httpx
import respx

from app.cli import cmd_diag_recebimentos
from tests import ml_fake, olist_fake
from tests.olist_helpers import connect_account
from tests.test_ml import connect_ml, ml_env  # noqa: F401 - fixture
from tests.olist_helpers import olist_env  # noqa: F401 - fixture

ORDER = {"id": 2000009, "status": "paid", "order_items": [], "shipping": {},
         "payments": [{"id": 777, "status": "approved", "date_approved": "2026-11-10T10:00:00.000-04:00",
                       "total_paid_amount": 100.0}]}


@respx.mock
def test_diagnostico_de_recebimentos(db, ml_env, olist_env, capsys):
    connect_ml(db)
    connect_account(db)
    routes = ml_fake.install(respx.mock)
    routes["orders"].mock(return_value=httpx.Response(
        200, json={"results": [ORDER], "paging": {"total": 1}}))
    mp = routes["mp_payment"].mock(return_value=httpx.Response(200, json={
        "status": "approved", "money_release_date": "2026-11-24T10:00:00.000-04:00",
        "money_release_status": "pending", "transaction_amount": 100.0,
        "transaction_details": {"net_received_amount": 82.5}, "payer": {"email": "nao-mostrar@exemplo.com"}}))
    olist_fake.install(respx.mock)
    receivables = respx.get(f"{olist_fake.BASE_URL}/contas-receber").mock(return_value=httpx.Response(200, json={
        "itens": [{"id": 1, "valor": 150.5, "dataVencimento": "2026-11-20", "cliente": {"nome": "Fulano"}},
                  {"id": 2, "valor": 49.5, "dataVencimento": "2026-11-15"}],
        "paginacao": {"total": 2}}))
    assert cmd_diag_recebimentos(None) == 0
    out = capsys.readouterr().out
    assert "datas: {'date_approved'" in out
    assert "liberação=2026-11-24" in out and "líquido=82.5" in out
    assert "2 conta(s), total R$ 200,00" in out and "de 2026-11-15 até 2026-11-20" in out
    assert "nao-mostrar" not in out and "Fulano" not in out  # nada de dados de clientes
    assert mp.calls[0].request.method == "GET" and receivables.calls[0].request.url.params["situacao"] == "aberto"
