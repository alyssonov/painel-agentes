"""Fase 7 — Agente de Preços (referências de preço do Mercado Livre + custo do Olist)."""

import json

import httpx
import pytest
import respx
from sqlalchemy import select

from app.agents.prices import analyze, run_prices_agent
from app.agents.registry import ensure_agents
from app.config import get_settings
from app.core import actions
from app.core.db import get_sessionmaker, utcnow
from app.models import Action, Agent, Listing, PriceReference, Product
from app.models.enums import ActionStatus, ActionType, Channel
from app.services.store_settings import set_setting
from tests import ml_fake
from tests.conftest import csrf_from
from tests.test_ml import connect_ml, ml_env, sync  # noqa: F401 - fixture


def ref(**kw):
    base = dict(channel=Channel.ML, item_id="MLB1", status="with_benchmark_highest", applicable=True,
                current_cents=6800, listed_cents=6800, suggested_cents=6162, lowest_cents=6162,
                selling_fee_cents=739, shipping_fee_cents=915, compared=5, checked_at=utcnow())
    base.update(kw)
    return PriceReference(**base)


# --- Análise com os números reais do diagnóstico de 11/11/2026 -----------------------

def test_baixa_quando_o_lucro_continua_acima_da_margem():
    # MLB4453661163: R$ 68 → 61,62; custo 34,60; lucro no sugerido 8,94 (14,5%)
    result = analyze(ref(), 3460, 2.5, 10)
    assert result.verdict == "baixar" and result.proposed_cents == 6162
    assert result.profit_at_suggested == 6162 - 739 - 915 - 3460 - 154


def test_com_margem_minima_maior_propoe_o_menor_preco_que_mantem_a_margem():
    result = analyze(ref(), 3460, 2.5, 15)  # 14,5% < 15%: baixa só até manter 15%
    assert result.verdict == "baixar_parcial" and 6162 < result.proposed_cents < 6800
    assert result.profit_at_proposed / result.proposed_cents >= 0.15


def test_concorrencia_abaixo_do_custo_so_avisa():
    # MLB4940452909: sugerido 97,80; custos 16,63 + 32,45; custo 55,33 → prejuízo
    r = ref(current_cents=11000, listed_cents=11000, suggested_cents=9780, selling_fee_cents=1663,
            shipping_fee_cents=3245, status="with_benchmark_high")
    result = analyze(r, 5533, 2.5, 15)
    assert result.verdict in ("abaixo_do_custo", "baixar_parcial")
    assert result.profit_at_suggested < 0


def test_em_promocao_automatico_e_variacoes_nao_mexem():
    assert analyze(ref(current_cents=11200, listed_cents=20000), 5826, 2.5, 15).verdict == "promocao"
    assert analyze(ref(automated=True), 3460, 2.5, 15).verdict == "automatico"
    assert analyze(ref(has_variations=True), 3460, 2.5, 15).verdict == "variacoes"
    assert analyze(ref(), None, 2.5, 15).verdict == "sem_custo"


def test_abaixo_da_concorrencia_sugere_subir():
    result = analyze(ref(status="no_benchmark_lowest", current_cents=5500, listed_cents=5500), 3000, 2.5, 15)
    assert result.verdict == "subir" and result.proposed_cents == 6162


# --- Leitura, agente e gateway -------------------------------------------------------

def setup_real(db):
    connect_ml(db)
    prod = Product(sku="CAN-01", name="Caneca", olist_id="o1", cost_cents=3460)
    db.add(prod)
    db.flush()
    db.add(Listing(channel=Channel.ML, external_id="MLB1", item_id="MLB1", sku="CAN-01", product_id=prod.id,
                   title="Caneca Térmica", price_cents=6800, status="active", synced_at=utcnow()))
    set_setting(db, "tax_rate_pct", 2.5)
    ensure_agents(db)
    agent = db.scalar(select(Agent).where(Agent.slug == "precos"))
    agent.enabled, agent.config = True, {"margem_minima_pct": 10}
    db.commit()
    return prod


@respx.mock
def test_sincroniza_analisa_e_propoe(db, ml_env):
    setup_real(db)
    ml_fake.install(respx.mock)
    assert sync().status == "ok"
    row = db.scalar(select(PriceReference))
    assert row.suggested_cents == 6162 and row.selling_fee_cents == 739 and row.product_id is not None
    run = run_prices_agent(get_sessionmaker())
    assert run.status == "ok", run.error
    db.expire_all()
    action = db.scalar(select(Action).where(Action.type == ActionType.UPDATE_PRICE))
    assert action.status == ActionStatus.PENDING and action.payload["price_cents"] == 6162
    # Rodar de novo não duplica a sugestão.
    run_prices_agent(get_sessionmaker())
    assert db.query(Action).filter(Action.type == ActionType.UPDATE_PRICE).count() == 1


@pytest.fixture
def real_mode(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _proposal(db, r):
    db.add(r)
    db.flush()
    return actions.propose(db, agent_slug="precos", type=ActionType.UPDATE_PRICE, channel=Channel.ML,
                           target_type="anuncio", target_id=r.id,
                           payload={"price_cents": 6162, "old_cents": 6800, "item_id": r.item_id})


@respx.mock
def test_modo_real_muda_o_preco_pelo_gateway(db, ml_env, real_mode):
    setup_real(db)
    route = respx.put("https://api.mercadolibre.com/items/MLB1").mock(
        return_value=httpx.Response(200, json={"id": "MLB1", "price": 61.62}))
    action = _proposal(db, ref())
    actions.approve(db, action, actor="user:1")
    db.commit()
    assert json.loads(route.calls[0].request.content) == {"price": 61.62}
    assert action.status == ActionStatus.EXECUTED
    assert db.scalar(select(Listing)).price_cents == 6162


@respx.mock
def test_automatizacao_ou_aviso_do_ml_nao_muda(db, ml_env, real_mode):
    setup_real(db)
    route = respx.put("https://api.mercadolibre.com/items/MLB1").mock(return_value=httpx.Response(
        200, json={"id": "MLB1", "price": 68, "warnings": [{"message": "price not updated"}]}))
    action = _proposal(db, ref())
    actions.approve(db, action, actor="user:1")
    assert action.status == ActionStatus.FAILED and "não aplicou" in action.result
    blocked = _proposal(db, ref(item_id="MLB2", automated=True))
    actions.approve(db, blocked, actor="user:1")
    assert blocked.status == ActionStatus.FAILED and route.call_count == 1


def test_pagina_aprovar_com_preco_editado(logged_client, db):
    r = ref(title="Caneca Térmica")
    action = _proposal(db, r)
    db.add(ref(item_id="MLB9", title="Luminária", verdict="abaixo_do_custo", profit_at_suggested_cents=-906))
    db.commit()
    html = logged_client.get("/estoque-precos").text
    assert "Sugestões de preço" in html and "R$ 68,00 → R$ 61,62" in html
    assert "baixar o preço não compensa" in html and "Luminária" in html
    token = csrf_from(html)
    r2 = logged_client.post(f"/estoque-precos/preco/{action.id}/aprovar", data={"csrf_token": token, "preco": "62,90"})
    assert "Modo simulação" in r2.text and "R$ 62,90" in r2.text
    db.expire_all()
    assert db.get(Action, action.id).payload["price_cents"] == 6290
    assert logged_client.post(f"/estoque-precos/preco/{action.id}/descartar", data={}).status_code == 403
