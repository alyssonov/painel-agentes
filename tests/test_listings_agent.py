"""Fase 7 — Agente 2 (Anúncios): qualidade do ML + sugestões de texto com aprovação."""

import json

import httpx
import pytest
import respx
from sqlalchemy import select

from app.agents.listings import candidates, run_listings_agent
from app.agents.registry import ensure_agents
from app.ai.client import RawResult
from app.config import get_settings
from app.core import actions
from app.core.db import get_sessionmaker, utcnow
from app.integrations.mercadolivre.auth import MLTokenProvider
from app.integrations.mercadolivre.client import BASE_URL, MLClient
from app.integrations.mercadolivre.writes import clean_listing_text
from app.models import Action, Agent, Listing, ListingQuality
from app.models.enums import ActionStatus, ActionType, AgentMode, Channel
from tests import ml_fake
from tests.conftest import csrf_from
from tests.test_ml import connect_ml, ml_env, sync  # noqa: F401 - fixture
from tests.test_phase4 import product, sale

ITEM = {"id": "MLB1", "title": "caneca termica inox", "sold_quantity": 0,
        "attributes": [{"id": "BRAND", "name": "Marca", "value_name": "Termix"},
                       {"id": "CAPACITY", "name": "Capacidade", "value_name": "500 mL"}]}


def ai(titulo="Caneca Térmica Inox 500 mL Termix", descricao="Caneca térmica de inox.\nCARACTERÍSTICAS\n- 500 mL",
       calls=None):
    def transport(model, effort, system, user, schema, max_tokens):
        if calls is not None:
            calls.append({"system": system, "user": user, "model": model})
        return RawResult(text=json.dumps({"titulo": titulo, "descricao": descricao,
                                          "o_que_mudou": ["Organizei as características"], "confianca": "alta"}),
                         stop_reason="end_turn", input_tokens=1500, output_tokens=600)
    return transport


def setup(db, mode=AgentMode.MANUAL):
    connect_ml(db)
    prod = product(db, "CAN-01", cost=2000)
    db.add(ListingQuality(channel=Channel.ML, item_id="MLB1", title="caneca termica inox", product_id=prod.id,
                          score=45, level="Bad", level_wording="Básica", sold_quantity=0,
                          pending=[{"bucket": "Fotos", "label": "Adicionar fotos", "title": "", "link": None}],
                          checked_at=utcnow()))
    ensure_agents(db)
    agent = db.scalar(select(Agent).where(Agent.slug == "anuncios"))
    agent.enabled, agent.mode = True, mode
    db.commit()
    return prod


def ml_routes(item=ITEM):
    respx.get(f"{BASE_URL}/items/MLB1").mock(return_value=httpx.Response(200, json=item))
    respx.get(f"{BASE_URL}/items/MLB1/description").mock(return_value=httpx.Response(200, json={"plain_text": "Caneca."}))


def client():
    return MLClient(MLTokenProvider(get_sessionmaker()), sleep=lambda _s: None)


@respx.mock
def test_le_a_qualidade_dos_anuncios(db, ml_env):
    connect_ml(db)
    prod = product(db, "CAN-01", cost=2000)
    db.add(Listing(channel=Channel.ML, external_id="MLB1", item_id="MLB1", sku="CAN-01", product_id=prod.id,
                   title="Caneca", price_cents=6990, status="active", synced_at=utcnow()))
    db.commit()
    ml_fake.install(respx.mock)
    assert sync().status == "ok"
    q = db.scalar(select(ListingQuality))
    assert q.score == 48 and q.level_wording == "Básica" and q.product_id == prod.id
    assert q.pending == [{"bucket": "Fotos", "label": "Adicionar fotos", "title": "Use pelo menos 5 fotos.",
                          "link": "https://www.mercadolivre.com.br/syi/core/modify?itemId=MLB1"}]


def test_prioriza_os_fracos_que_mais_vendem(db):
    a, b = product(db, "A"), product(db, "B")
    sale(db, b, qty=10, days_ago=2)
    sale(db, a, qty=1, days_ago=2)
    for item, prod, score in (("MLBA", a, 30), ("MLBB", b, 60), ("MLBC", a, 95)):
        db.add(ListingQuality(channel=Channel.ML, item_id=item, product_id=prod.id, score=score, pending=[],
                              checked_at=utcnow()))
    db.commit()
    assert [q.item_id for q in candidates(db, 70)] == ["MLBB", "MLBA"]


@respx.mock
def test_sugere_e_nunca_aprova_sozinho_mesmo_no_automatico(db, ml_env):
    setup(db, mode=AgentMode.AUTO)
    ml_routes()
    calls = []
    run = run_listings_agent(get_sessionmaker(), transport=ai(calls=calls), ml_client_factory=client)
    assert run.status == "ok", run.error
    db.expire_all()
    action = db.scalar(select(Action).where(Action.type == ActionType.UPDATE_LISTING))
    assert action.status == ActionStatus.PENDING  # anúncio nunca publica sozinho
    assert action.payload["title"] == "Caneca Térmica Inox 500 mL Termix" and action.payload["old_description"] == "Caneca."
    assert "Capacidade: 500 mL" in calls[0]["user"] and "Adicionar fotos" in calls[0]["user"]
    assert calls[0]["model"].startswith("claude-sonnet")
    # Já tem sugestão: não gera outra.
    run_listings_agent(get_sessionmaker(), transport=ai(calls=calls), ml_client_factory=client)
    assert len(calls) == 1


@respx.mock
def test_com_vendas_nao_sugere_titulo_e_descarta_contato(db, ml_env):
    setup(db)
    ml_routes({**ITEM, "sold_quantity": 12})
    calls = []
    run_listings_agent(get_sessionmaker(), transport=ai(calls=calls), ml_client_factory=client)
    db.expire_all()
    action = db.scalar(select(Action))
    assert action.payload["title"] is None and "NÃO pode mudar" in calls[0]["system"]
    actions.reject(db, action, actor="user:1")
    db.commit()
    # Descartada: não volta a sugerir para esse anúncio por 30 dias.
    run_listings_agent(get_sessionmaker(), transport=ai(descricao="Chame no whats 11 99999-9999", calls=calls),
                       ml_client_factory=client)
    assert len(calls) == 1


def test_texto_sem_emoji_nem_menor_maior():
    assert clean_listing_text("Ótima 😀 <b>caneca</b>\r\nlinha") == "Ótima  bcaneca/b\nlinha"


@pytest.fixture
def real_mode(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _proposal(db, title="Caneca Térmica Inox 500 mL"):
    q = db.scalar(select(ListingQuality))
    return actions.propose(db, agent_slug="anuncios", type=ActionType.UPDATE_LISTING, channel=Channel.ML,
                           target_type="anuncio_texto", target_id=q.id,
                           payload={"item_id": "MLB1", "description": "Nova descrição.", "title": title})


@respx.mock
def test_modo_real_publica_descricao_e_titulo_se_nao_tem_vendas(db, ml_env, real_mode):
    setup(db)
    respx.get(f"{BASE_URL}/items/MLB1").mock(return_value=httpx.Response(200, json=ITEM))
    desc = respx.put(f"{BASE_URL}/items/MLB1/description").mock(return_value=httpx.Response(200, json={}))
    title = respx.put(f"{BASE_URL}/items/MLB1").mock(return_value=httpx.Response(200, json={"id": "MLB1"}))
    action = _proposal(db)
    actions.approve(db, action, actor="user:1")
    assert action.status == ActionStatus.EXECUTED
    assert json.loads(desc.calls[0].request.content) == {"plain_text": "Nova descrição."}
    assert desc.calls[0].request.url.params["api_version"] == "2"
    assert json.loads(title.calls[0].request.content) == {"title": "Caneca Térmica Inox 500 mL"}


@respx.mock
def test_modo_real_com_vendas_so_muda_a_descricao(db, ml_env, real_mode):
    setup(db)
    respx.get(f"{BASE_URL}/items/MLB1").mock(return_value=httpx.Response(200, json={**ITEM, "sold_quantity": 3}))
    respx.put(f"{BASE_URL}/items/MLB1/description").mock(return_value=httpx.Response(200, json={}))
    title = respx.put(f"{BASE_URL}/items/MLB1").mock(return_value=httpx.Response(200, json={}))
    action = _proposal(db)
    actions.approve(db, action, actor="user:1")
    assert action.status == ActionStatus.EXECUTED and "já tem vendas" in action.result
    assert title.call_count == 0


def test_pagina_de_anuncios_aprovar_editado(logged_client, db):
    setup(db)
    action = _proposal(db)
    db.commit()
    html = logged_client.get("/anuncios").text
    assert "Sugestões do agente" in html and "Nova descrição." in html and "Adicionar fotos" in html
    assert "style=" not in html
    token = csrf_from(html)
    r = logged_client.post(f"/anuncios/acao/{action.id}/aprovar",
                           data={"csrf_token": token, "descricao": "Texto revisado por mim.", "titulo": "Caneca Termix"})
    assert "Modo simulação" in r.text
    db.expire_all()
    saved = db.get(Action, action.id)
    assert saved.payload["description"] == "Texto revisado por mim." and saved.payload["title"] == "Caneca Termix"
    assert logged_client.post(f"/anuncios/acao/{action.id}/descartar", data={}).status_code == 403
