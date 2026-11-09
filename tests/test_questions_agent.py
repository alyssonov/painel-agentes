"""Agente 1 (Respondedor de perguntas) + ActionGateway + fila no painel."""

import ast
import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select

from app.agents.questions import process_question, run_questions_agent
from app.ai.client import AIClient, RawResult
from app.config import get_settings
from app.core import actions
from app.core.db import get_sessionmaker, utcnow
from app.integrations.mercadolivre.auth import MLTokenProvider
from app.integrations.mercadolivre.client import BASE_URL, MLClient
from app.integrations.mercadolivre.writes import ANSWERS_URL
from app.models import Action, Agent, AuditLog, Product, Question, StockSnapshot
from app.models.enums import ActionStatus, AgentMode, Channel, QuestionStatus
from tests.conftest import csrf_from
from tests.test_ml import connect_ml, ml_env  # noqa: F401 - fixture

ITEM = {"id": "MLB1", "title": "Caneca Térmica 500ml", "price": 69.9, "condition": "new",
        "available_quantity": 5, "shipping": {"free_shipping": False},
        "attributes": [{"id": "BRAND", "name": "Marca", "value_name": "Termix"},
                       {"id": "SELLER_SKU", "name": "SKU", "value_name": "CAN-01"}]}


def ai_answer(resposta="Olá! Sim, mantém gelado por 12 horas.", confianca="alta", assunto="produto", motivo="ok"):
    calls = []

    def transport(model, effort, system, user, schema, max_tokens):
        calls.append({"system": system, "user": user})
        return RawResult(text=json.dumps({"resposta": resposta, "confianca": confianca, "assunto": assunto,
                                          "motivo": motivo}), stop_reason="end_turn",
                         input_tokens=800, output_tokens=60)
    transport.calls = calls
    return transport


def add_question(db, text="Serve para bebida gelada?", qid="9001"):
    q = Question(channel=Channel.ML, external_id=qid, listing_external_id="MLB1", text=text,
                 asked_at=utcnow() - timedelta(minutes=5), status=QuestionStatus.NEW)
    db.add(q)
    db.commit()
    return q


def enable_agent(db, mode=AgentMode.MANUAL):
    from app.agents.registry import ensure_agents
    ensure_agents(db)
    agent = db.scalar(select(Agent).where(Agent.slug == "perguntas"))
    agent.enabled, agent.mode = True, mode
    db.commit()
    return agent


def ml_routes():
    respx.get(f"{BASE_URL}/items/MLB1").mock(return_value=httpx.Response(200, json=ITEM))
    respx.get(f"{BASE_URL}/items/MLB1/description").mock(
        return_value=httpx.Response(200, json={"plain_text": "Caneca de inox, parede dupla."}))


def ml_client():
    return MLClient(MLTokenProvider(get_sessionmaker()), sleep=lambda _s: None)


# --- Regras do projeto ------------------------------------------------------------

def test_escrita_no_ml_so_pelo_gateway():
    """Só app/core/actions.py pode importar o módulo de escrita do Mercado Livre."""
    importers = []
    for path in Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            if any("mercadolivre.writes" in n for n in names):
                importers.append(str(path))
    assert importers == ["app/core/actions.py"]


# --- Agente ---------------------------------------------------------------------

@respx.mock
def test_gera_sugestao_com_ficha_e_estoque_do_olist(db, ml_env):
    connect_ml(db)
    product = Product(sku="CAN-01", name="Caneca", olist_id="101")
    db.add(product)
    db.flush()
    db.add(StockSnapshot(product_id=product.id, quantity=0, reserved=0, captured_at=utcnow()))
    q = add_question(db)
    ml_routes()
    transport = ai_answer()
    with ml_client() as ml:
        action = process_question(db, q, ml, AIClient(db, transport), "Loja Teste")
    db.commit()
    assert action.status == ActionStatus.PENDING and action.payload["text"].startswith("Olá!")
    assert q.status == QuestionStatus.PROPOSED and not action.needs_human
    sent = transport.calls[0]
    assert "Marca: Termix" in sent["user"] and "Caneca de inox" in sent["user"]
    assert "ESTOQUE: esgotado no momento" in sent["user"]  # veio do Olist, não do anúncio (5)
    assert "SKU" not in sent["user"].split("Ficha técnica:")[1].split("Descrição")[0]
    assert "Loja Teste" in sent["system"]


@respx.mock
def test_desconto_vai_para_pessoa_sem_gastar_ia(db, ml_env):
    connect_ml(db)
    q = add_question(db, "Faz desconto se eu levar 3?")
    transport = ai_answer()
    with ml_client() as ml:
        action = process_question(db, q, ml, AIClient(db, transport), "Loja")
    assert action.needs_human and q.status == QuestionStatus.HUMAN
    assert transport.calls == []  # nem chamou a IA


@pytest.mark.parametrize("kwargs, motivo", [
    ({"confianca": "baixa"}, "não tem certeza"),
    ({"assunto": "fora_da_ficha"}, "Assunto delicado"),
    ({"resposta": "Chama no WhatsApp (11) 99999-8888"}, "contato"),
])
@respx.mock
def test_casos_que_precisam_de_pessoa(db, ml_env, kwargs, motivo):
    connect_ml(db)
    q = add_question(db)
    ml_routes()
    with ml_client() as ml:
        action = process_question(db, q, ml, AIClient(db, ai_answer(**kwargs)), "Loja")
    assert action.needs_human and motivo in action.reason
    if "resposta" in kwargs:
        assert action.payload["text"] == ""  # sugestão com contato é descartada


@respx.mock
def test_rodada_do_agente_e_resumo(db, ml_env):
    connect_ml(db)
    enable_agent(db)
    add_question(db)
    add_question(db, "Tem garantia?", qid="9002")
    ml_routes()
    run = run_questions_agent(get_sessionmaker(), transport=ai_answer(), ml_client_factory=ml_client)
    assert run.status == "ok" and run.items_processed == 2
    assert run.summary == "0 respondidas hoje · 2 aguardando você"
    # Segunda rodada não duplica sugestões.
    run2 = run_questions_agent(get_sessionmaker(), transport=ai_answer(), ml_client_factory=ml_client)
    assert run2.items_processed == 0
    assert db.query(Action).count() == 2


def test_agente_pausado_nao_roda(db):
    from app.agents.registry import ensure_agents
    ensure_agents(db)
    assert run_questions_agent(get_sessionmaker(), transport=ai_answer()) is None


@respx.mock
def test_sem_chave_da_ia_avisa_no_resumo(db, ml_env):
    connect_ml(db)
    enable_agent(db)
    add_question(db)
    ml_routes()
    run = run_questions_agent(get_sessionmaker(), ml_client_factory=ml_client)
    assert "ANTHROPIC_API_KEY" in run.summary
    assert db.query(Action).count() == 0


# --- Gateway --------------------------------------------------------------------

def _pending_action(db, q, text="Olá! Sim."):
    action = actions.propose(db, agent_slug="perguntas", type="responder_pergunta", channel=Channel.ML,
                             target_type="pergunta", target_id=q.id, payload={"text": text}, confidence="alta")
    db.commit()
    return action


@respx.mock
def test_modo_simulacao_nunca_envia(db, ml_env):
    connect_ml(db)
    q = add_question(db)
    route = respx.post(ANSWERS_URL).mock(return_value=httpx.Response(200, json={}))
    action = _pending_action(db, q)
    actions.approve(db, action, actor="user:1")
    db.commit()
    assert action.status == ActionStatus.SIMULATED and route.call_count == 0
    assert q.status == QuestionStatus.SIMULATED and q.answer_text == "Olá! Sim."


@pytest.fixture
def real_mode(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@respx.mock
def test_modo_real_envia_a_resposta_editada(db, ml_env, real_mode):
    connect_ml(db)
    q = add_question(db)
    route = respx.post(ANSWERS_URL).mock(return_value=httpx.Response(200, json={"id": 1}))
    action = _pending_action(db, q)
    actions.approve(db, action, actor="user:1", edited_payload={"text": "Olá! Mantém gelado sim."})
    db.commit()
    body = json.loads(route.calls[0].request.content)
    assert body == {"question_id": 9001, "text": "Olá! Mantém gelado sim."}
    assert action.status == ActionStatus.EXECUTED and q.status == QuestionStatus.ANSWERED
    assert action.original_payload["text"] == "Olá! Sim."  # sugestão original guardada
    events = [e.event for e in db.scalars(select(AuditLog))]
    assert "acao.aprovada_editada" in events and "acao.executada" in events


@respx.mock
def test_falha_no_envio_pode_tentar_de_novo(db, ml_env, real_mode):
    connect_ml(db)
    q = add_question(db)
    respx.post(ANSWERS_URL).mock(side_effect=[httpx.Response(503), httpx.Response(200, json={})])
    action = _pending_action(db, q)
    actions.approve(db, action, actor="user:1")
    assert action.status == ActionStatus.FAILED and q.status == QuestionStatus.NEW
    actions.approve(db, action, actor="user:1")
    assert action.status == ActionStatus.EXECUTED


def test_nao_aprova_duas_vezes(db):
    q = add_question(db)
    action = _pending_action(db, q)
    actions.approve(db, action, actor="user:1")
    with pytest.raises(actions.ActionNotAllowed):
        actions.approve(db, action, actor="user:1")


@respx.mock
def test_modo_automatico_so_aprova_confianca_alta(db, ml_env):
    connect_ml(db)
    enable_agent(db, AgentMode.AUTO)
    q1, q2 = add_question(db), add_question(db, "Tem azul?", qid="9002")
    ml_routes()
    with ml_client() as ml:
        a1 = process_question(db, q1, ml, AIClient(db, ai_answer(confianca="alta")), "Loja")
        a2 = process_question(db, q2, ml, AIClient(db, ai_answer(confianca="media")), "Loja")
    assert a1.status == ActionStatus.SIMULATED and a1.decided_by == "agent:perguntas"
    assert a2.status == ActionStatus.PENDING


# --- Telas ---------------------------------------------------------------------------

def test_fila_e_aprovacao_no_painel(logged_client, db):
    q = add_question(db)
    action = _pending_action(db, q, "Olá! Sim, mantém gelado.")
    html = logged_client.get("/perguntas").text
    assert "Serve para bebida gelada?" in html and "Olá! Sim, mantém gelado." in html
    assert "Aprovar" in html and "Editar" in html and "Responder eu mesmo" in html
    token = csrf_from(html)
    r = logged_client.post(f"/perguntas/acao/{action.id}/enviar",
                           data={"csrf_token": token, "text": "Olá! Sim, mantém gelado."})
    assert "Modo simulação: nada foi enviado" in r.text
    assert "Respondidas hoje" in r.text and "simulação (não enviada)" in r.text


def test_responder_eu_mesmo_sem_sugestao(logged_client, db):
    q = add_question(db, "Pergunta sem sugestão")
    token = csrf_from(logged_client.get("/perguntas").text)
    r = logged_client.post(f"/perguntas/{q.id}/responder", data={"csrf_token": token, "text": "Resposta minha"})
    assert "Resposta aprovada" in r.text
    db.expire_all()
    assert db.get(Question, q.id).answer_text == "Resposta minha"


def test_tirar_da_fila(logged_client, db):
    q = add_question(db)
    action = _pending_action(db, q)
    token = csrf_from(logged_client.get("/perguntas").text)
    logged_client.post(f"/perguntas/acao/{action.id}/ignorar", data={"csrf_token": token})
    db.expire_all()
    assert db.get(Action, action.id).status == ActionStatus.REJECTED
    assert db.get(Question, q.id).status == QuestionStatus.CLOSED


def test_resposta_vazia_e_recusada(logged_client, db):
    q = add_question(db)
    action = _pending_action(db, q)
    token = csrf_from(logged_client.get("/perguntas").text)
    r = logged_client.post(f"/perguntas/acao/{action.id}/enviar", data={"csrf_token": token, "text": "  "})
    assert "Escreva uma resposta" in r.text
    assert logged_client.post(f"/perguntas/acao/{action.id}/enviar", data={"text": "x"}).status_code == 403


def test_pagina_de_agentes_liga_e_muda_modo(logged_client, db):
    html = logged_client.get("/agentes").text
    assert "Respondedor de perguntas" in html and "Chega na fase 5" in html
    token = csrf_from(html)
    logged_client.post("/agentes/perguntas/ativo", data={"csrf_token": token, "enabled": "1"})
    logged_client.post("/agentes/perguntas/modo", data={"csrf_token": token, "mode": "automatico"})
    r = logged_client.post("/agentes/perguntas/regras", data={"csrf_token": token, "answer_tone": "simpático"})
    assert "Regras salvas" in r.text
    db.expire_all()
    agent = db.scalar(select(Agent).where(Agent.slug == "perguntas"))
    assert agent.enabled and agent.mode == AgentMode.AUTO
    # Agentes de fases futuras não podem ser ligados.
    r = logged_client.post("/agentes/relatorio/ativo", data={"csrf_token": token, "enabled": "0"})
    assert "ainda não está disponível" in r.text
