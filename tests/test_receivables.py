"""Previsão de recebimentos (Mercado Livre → Mercado Pago)."""

from datetime import timedelta

import httpx
import respx
from sqlalchemy import select

from app.core.db import utcnow
from app.models import Payment
from app.models.enums import Channel
from app.services.receivables import forecast
from tests import ml_fake
from tests.test_ml import connect_ml, ml_env, sync  # noqa: F401 - fixture


@respx.mock
def test_sincronizacao_le_pagamentos_e_liberacao(db, ml_env):
    connect_ml(db)
    routes = ml_fake.install(respx.mock)
    run = sync()
    assert run.status == "ok", run.error
    pay = db.scalar(select(Payment).where(Payment.payment_id == "8000001"))
    assert pay.order_ref == "2000001" and pay.amount_cents == 9490 and pay.net_cents == 6876
    assert pay.release_status == "pending" and pay.release_date.isoformat().startswith("2026-12-08T21:59")
    assert routes["mp_payment"].calls[0].request.method == "GET"
    # 1ª vez lê as vendas de 35 dias para pegar o que ainda vai ser liberado.
    windows = [c.request.url.params["order.date_created.from"] for c in routes["orders"].calls]
    assert len(set(windows)) >= 2
    # Rodada seguinte não consulta de novo o que acabou de ser conferido.
    calls = routes["mp_payment"].call_count
    sync()
    assert routes["mp_payment"].call_count == calls


@respx.mock
def test_recusa_do_mercado_pago_nao_desconecta_o_mercado_livre(db, ml_env):
    connect_ml(db)
    routes = ml_fake.install(respx.mock)
    routes["mp_payment"].mock(return_value=httpx.Response(403, json={"message": "forbidden"}))
    run = sync()
    assert run.status == "ok", run.error
    assert db.scalar(select(Payment)).release_date is None


def add(db, release_days, net, status="pending", pay_status="approved", net_known=True):
    now = utcnow()
    db.add(Payment(channel=Channel.ML, payment_id=f"p{len(db.new)}{release_days}{net}", order_ref="X",
                   status=pay_status, amount_cents=net * 2, net_cents=net if net_known else None,
                   release_date=now + timedelta(days=release_days) if release_days is not None else None,
                   release_status=status, checked_at=now))


def test_previsao_dia_a_dia(db):
    add(db, 2, 1000)
    add(db, 2, 500)
    add(db, 20, 3000)
    add(db, 45, 7000)                      # depois de 30 dias: só no total
    add(db, -3, 800)                       # data passou e não liberou
    add(db, -2, 400, status="released")    # liberado na semana
    add(db, 5, 9999, pay_status="refunded")  # estornado: fora
    add(db, None, 100)                     # ainda sem data
    db.commit()
    f = forecast(db)
    assert f.next_7_cents == 1500 and f.next_30_cents == 4500
    assert f.pending_total_cents == 1000 + 500 + 3000 + 7000 + 800
    assert (f.overdue_count, f.overdue_cents) == (1, 800)
    assert f.released_7_cents == 400 and f.reading == 1
    assert [(d.payments, d.net_cents) for d in f.days] == [(2, 1500), (1, 3000)]


def test_pagina_de_recebimentos(logged_client, db):
    html = logged_client.get("/margem/recebimentos").text  # simulação: dados fictícios
    assert "Previsão de recebimentos" in html and "Próximos 7 dias" in html and "style=" not in html
    assert "Previsão de recebimentos" in logged_client.get("/margem").text
