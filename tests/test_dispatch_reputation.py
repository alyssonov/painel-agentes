"""Alerta de despacho e Termômetro da reputação (Mercado Livre)."""

from datetime import datetime, timedelta

import respx
from sqlalchemy import select

from app.agents.dispatch import build_alerts as dispatch_alerts
from app.agents.dispatch import run_dispatch_alerts
from app.agents.registry import ensure_agents
from app.agents.reputation import build_alerts as reputation_alerts
from app.agents.reputation import run_reputation_check
from app.core.db import get_sessionmaker, utcnow
from app.integrations.mercadolivre.mapping import reputation_metrics
from app.models import Agent, Alert, Order, Shipment
from app.models.enums import Channel
from tests import ml_fake
from tests.test_ml import connect_ml, ml_env, sync  # noqa: F401 - fixture


def enable(db, *slugs):
    ensure_agents(db)
    for agent in db.scalars(select(Agent).where(Agent.slug.in_(slugs))):
        agent.enabled = True
    db.commit()


def shipment(db, sid, deadline, status="ready_to_ship", logistic="xd_drop_off", ref=None):
    db.add(Shipment(channel=Channel.ML, shipment_id=sid, order_ref=ref or f"P{sid}", status=status,
                    logistic_type=logistic, dispatch_deadline=deadline, checked_at=utcnow()))
    db.flush()


# --- Leitura -----------------------------------------------------------------------

@respx.mock
def test_sincronizacao_le_envios_e_reputacao(db, ml_env):
    connect_ml(db)
    ml_fake.install(respx.mock)
    run = sync()
    assert run.status == "ok", run.error
    rows = {s.shipment_id: s for s in db.scalars(select(Shipment))}
    assert rows["4100001"].status == "ready_to_ship"
    assert rows["4100001"].dispatch_deadline == datetime(2026, 11, 10, 18, 0)  # 15:00 Brasília = 18:00 UTC
    assert rows["4100002"].logistic_type == "fulfillment" and rows["4100002"].dispatch_deadline is None
    from app.integrations.mercadolivre.auth import get_ml_account
    metrics = get_ml_account(db).extra["reputation_metrics"]
    assert metrics["reclamacoes"] == 1.75 and metrics["atrasos"] == 3.0 and metrics["cancelamentos"] == 0.2


def test_metricas_sem_dados():
    assert reputation_metrics({})["reclamacoes"] is None


# --- Despacho ---------------------------------------------------------------------------

def test_alertas_de_despacho(db):
    now = utcnow()
    db.add(Order(channel=Channel.ML, external_id="P1", olist_id="o1", olist_number="5001", customer_short_name="A",
                 ordered_at=now))
    shipment(db, "1", now - timedelta(hours=3), ref="P1")  # atrasado
    shipment(db, "2", now + timedelta(hours=5))  # vence em 5 h
    shipment(db, "3", now + timedelta(days=5))  # longe
    shipment(db, "4", now - timedelta(hours=1), logistic="fulfillment")  # Full: não é com a loja
    shipment(db, "5", now - timedelta(hours=1), status="shipped")  # já saiu
    specs, late, soon = dispatch_alerts(db, warn_hours=24)
    assert (late, soon) == (1, 1)
    by_key = {s.key: s for s in specs}
    assert by_key["envio:1"].severity == "critico" and "Pedido 5001" in by_key["envio:1"].title
    assert "venceu há 3 h" in by_key["envio:1"].title
    assert by_key["envio:2"].severity == "atencao" and "despachar" in by_key["envio:2"].title


def test_agente_de_despacho(db, ml_env):
    connect_ml(db)
    enable(db, "despacho")
    shipment(db, "9", utcnow() - timedelta(hours=2))
    db.commit()
    run = run_dispatch_alerts(get_sessionmaker())
    assert run.summary == "0 para enviar hoje · 0 já enviado(s) · 1 atrasado(s)"
    assert db.scalar(select(Alert).where(Alert.source == "despacho")).severity == "critico"
    # Despachou: o alerta some sozinho.
    db.scalar(select(Shipment)).status = "shipped"
    db.commit()
    run = run_dispatch_alerts(get_sessionmaker())
    db.expire_all()
    assert db.scalar(select(Alert).where(Alert.source == "despacho")).status == "resolvido"


# --- Reputação ----------------------------------------------------------------------------

RULES = {"limite_reclamacoes": 2, "limite_atrasos": 10, "limite_cancelamentos": 1.5, "aviso_pct": 80}


def test_termometro_avisa_antes_de_passar_do_limite():
    specs, parts = reputation_alerts({"reclamacoes": 1.75, "atrasos": 3.0, "cancelamentos": 1.6,
                                      "periodo": "60 days"}, RULES)
    by_key = {s.key: s for s in specs}
    assert by_key["reclamacoes"].severity == "atencao" and "perto do limite" in by_key["reclamacoes"].title
    assert by_key["cancelamentos"].severity == "critico" and "acima do limite" in by_key["cancelamentos"].title
    assert "atrasos" not in by_key
    assert "últimos 60 dias" in by_key["reclamacoes"].detail
    assert len(parts) == 3


@respx.mock
def test_agente_de_reputacao(db, ml_env):
    connect_ml(db)
    enable(db, "reputacao")
    ml_fake.install(respx.mock)
    sync()
    run = run_reputation_check(get_sessionmaker())
    assert "Reclamações 1,75%" in run.summary
    alert = db.scalar(select(Alert).where(Alert.source == "reputacao"))
    assert alert.severity == "atencao"  # 1,75% é mais de 80% do limite de 2%


def test_agentes_novos_aparecem_em_agentes(logged_client):
    html = logged_client.get("/agentes").text
    assert "Alerta de despacho" in html and "Termômetro da reputação" in html
    assert "Limite do verde para reclamações" in html


# --- Quadro "Envios de hoje" (pelo prazo de despacho) ----------------------------------------

def test_quadro_mostra_o_que_precisa_sair_hoje(db, ml_env):
    from app.core.timeutil import local_day_bounds_utc, local_today
    from app.services.dashboard import dispatch_day, kanban
    connect_ml(db)  # conta real: o quadro passa a ser "Envios de hoje"
    start, end = local_day_bounds_utc(local_today())
    now = utcnow()
    later_today = min(now + timedelta(hours=1), end - timedelta(minutes=1))
    db.add(Order(channel=Channel.ML, external_id="P1", olist_id="o1", olist_number="5001",
                 customer_short_name="Ana S.", ordered_at=now - timedelta(days=2), items_total_cents=5000))
    shipment(db, "1", later_today, ref="P1")  # vendido há 2 dias, sai hoje → A separar
    db.add(Shipment(channel=Channel.ML, shipment_id="2", order_ref="P2", status="ready_to_ship",
                    substatus="printed", dispatch_deadline=later_today, checked_at=now))  # etiqueta impressa
    shipment(db, "3", start + timedelta(minutes=1), status="shipped")  # já saiu hoje
    shipment(db, "4", start - timedelta(hours=5))  # de ontem, ainda não saiu → atrasado
    shipment(db, "5", end + timedelta(hours=10))  # só amanhã: fora do quadro
    shipment(db, "6", later_today, logistic="fulfillment")  # Full: fora
    db.commit()
    cols = {c.status: c for c in kanban(db)}
    assert [o.external_id for o in cols["a_separar"].orders] == ["P1"]
    assert [o.external_id for o in cols["embalado"].orders] == ["P2"]
    assert [o.external_id for o in cols["enviado"].orders] == ["P3"]
    assert [o.external_id for o in cols["problema"].orders] == ["P4"]
    assert cols["problema"].orders[0].problem.startswith("Prazo venceu")
    assert cols["a_separar"].orders[0].time_label.startswith("até ")
    day = dispatch_day(db)
    assert (day.to_ship, day.shipped, day.late) == (2, 1, 1)
    assert kanban(db, "shopee")[0].orders == []  # a Shopee ainda não informa prazo


def test_visao_geral_mostra_envios_de_hoje(logged_client, db, ml_env):
    connect_ml(db)
    html = logged_client.get("/").text
    assert "Envios de hoje" in html and "para enviar hoje" in html
