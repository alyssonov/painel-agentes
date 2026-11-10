"""Fase 6 — Pós-venda: leitura de mensagens e reclamações, Agente 5, envio pelo gateway e página."""

import json
from datetime import timedelta

import httpx
import pytest
import respx
from sqlalchemy import select

from app.agents.postsale import claim_alerts, process_conversation, run_postsale_agent
from app.agents.registry import ensure_agents
from app.ai.client import AIClient
from app.config import get_settings
from app.core import actions
from app.core.db import get_sessionmaker, utcnow
from app.integrations.mercadolivre.writes import MLB_MESSAGING_AGENT_ID, clean_message
from app.models import Action, Agent, Claim, Conversation, ConversationMessage, Order, OrderItem, Shipment
from app.models.enums import ActionStatus, ActionType, AlertSeverity, Channel, ConversationStatus
from app.sync.ml_postsale import apply_conversation
from tests import ml_fake
from tests.conftest import csrf_from
from tests.test_ml import connect_ml, ml_env, sync  # noqa: F401 - fixture
from tests.test_questions_agent import ai_answer

SELLER = "123456"
BUYER_AGENT = 3037675074


def conversation(db, pack="P1", text="Quando chega meu pedido?", status=ConversationStatus.NEW, seller_first=None):
    conv = Conversation(channel=Channel.ML, pack_ref=pack, status=status, remote_status="active",
                        last_from_buyer=True, checked_at=utcnow(), last_message_at=utcnow() - timedelta(minutes=5))
    if seller_first:
        conv.messages.append(ConversationMessage(external_id="s0", from_seller=True, text=seller_first,
                                                 sent_at=utcnow() - timedelta(hours=2)))
    conv.messages.append(ConversationMessage(external_id="b1", from_seller=False, text=text,
                                             sent_at=utcnow() - timedelta(minutes=5)))
    db.add(conv)
    db.commit()
    return conv


def ml_data(*messages, status="active", substatus=None, claim_id=None):
    return {"conversation_status": {"status": status, "substatus": substatus, "claim_id": claim_id},
            "messages": [{"id": mid, "from": {"user_id": SELLER if seller else BUYER_AGENT}, "status": "available",
                          "text": text, "message_date": {"created": when}} for mid, seller, text, when in messages],
            "seller_max_message_length": 350}


def iso(minutes_from_now: int) -> str:
    return (utcnow() + timedelta(minutes=minutes_from_now)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def enable(db, mode="manual"):
    ensure_agents(db)
    agent = db.scalar(select(Agent).where(Agent.slug == "posvenda"))
    agent.enabled, agent.mode = True, mode
    db.commit()


# --- Leitura -----------------------------------------------------------------------

@respx.mock
def test_sincronizacao_le_conversas_e_reclamacoes(db, ml_env):
    connect_ml(db)
    routes = ml_fake.install(respx.mock)
    run = sync()
    assert run.status == "ok", run.error
    assert run.counts["mensagens"] == 1 and run.counts["reclamacoes"] == 1
    # O painel nunca marca as mensagens como lidas no Mercado Livre.
    request = routes["pack_messages"].calls[0].request
    assert request.url.params["mark_as_read"] == "false" and request.url.params["tag"] == "post_sale"
    conv = db.scalar(select(Conversation))
    assert conv.pack_ref == "3000001" and conv.status == ConversationStatus.NEW and conv.last_from_buyer
    assert [(m.from_seller, m.text) for m in conv.messages] == [
        (True, "Olá! Obrigado pela compra."), (False, "Quando chega meu pedido?")]
    claim = db.scalar(select(Claim))
    assert claim.status == "opened" and claim.problem == "O comprador diz que o produto não chegou"
    assert claim.affects_reputation == "affected" and claim.action_responsible == "seller"
    assert claim.seller_actions[0]["mandatory"] is True
    # Reclamação que sumiu da lista de abertas foi encerrada.
    ml_fake.install(respx.mock)["claims"].mock(return_value=httpx.Response(
        200, json={"paging": {"total": 0}, "data": []}))
    from app.integrations.mercadolivre.auth import get_ml_account
    from app.sync.common import set_cursor
    account = get_ml_account(db)
    set_cursor(account, "cursor_reclamacoes", utcnow() - timedelta(hours=1))
    db.commit()
    assert sync().status == "ok"
    db.expire_all()
    assert db.scalar(select(Claim)).status == "closed"


def test_respondida_direto_no_ml_sai_da_fila(db):
    conv = conversation(db, status=ConversationStatus.PROPOSED)
    action = actions.propose(db, agent_slug="posvenda", type=ActionType.SEND_MESSAGE, channel=Channel.ML,
                             target_type="conversa", target_id=conv.id, payload={"text": "Olá!"})
    db.commit()
    apply_conversation(db, "P1", ml_data(("b1", False, "Quando chega?", iso(-5)),
                                         ("s9", True, "Chega amanhã!", iso(-1))), SELLER, utcnow())
    db.commit()
    assert conv.status == ConversationStatus.ANSWERED and not conv.last_from_buyer
    assert db.get(Action, action.id).status == ActionStatus.REJECTED


def test_mensagem_nova_do_comprador_refaz_a_sugestao(db):
    conv = conversation(db, status=ConversationStatus.PROPOSED)
    action = actions.propose(db, agent_slug="posvenda", type=ActionType.SEND_MESSAGE, channel=Channel.ML,
                             target_type="conversa", target_id=conv.id, payload={"text": "Olá!"})
    db.commit()
    apply_conversation(db, "P1", ml_data(("b2", False, "E a nota fiscal?", iso(-1))),
                       SELLER, utcnow())
    db.commit()
    assert conv.status == ConversationStatus.NEW
    assert db.get(Action, action.id).status == ActionStatus.REJECTED


def test_conversa_bloqueada_nao_pode_ser_respondida(db):
    conv = conversation(db)
    apply_conversation(db, "P1", ml_data(status="blocked", substatus="blocked_by_mediation"), SELLER, utcnow())
    db.commit()
    assert conv.status == ConversationStatus.BLOCKED and conv.blocked_reason == "blocked_by_mediation"


# --- Agente 5 -----------------------------------------------------------------------

def test_agente_sugere_resposta_com_dados_do_pedido(db):
    conv = conversation(db, seller_first="Obrigado pela compra!")
    order = Order(channel=Channel.ML, external_id="P1", olist_id="o1", olist_number="1234",
                  customer_short_name="Maria S.", ordered_at=utcnow() - timedelta(days=1), invoice_olist_id="nf1")
    order.items.append(OrderItem(sku="CAN-01", title="Caneca Térmica", quantity=2, unit_price_cents=6990))
    db.add(order)
    db.add(Shipment(channel=Channel.ML, shipment_id="S1", order_ref="P1", status="ready_to_ship",
                    substatus="picked_up", shipped_at=utcnow(), checked_at=utcnow()))
    db.commit()
    transport = ai_answer("Olá! Seu pedido já foi despachado e está a caminho.", "alta", "rastreio")
    action = process_conversation(db, conv, AIClient(db, transport), "Loja Teste")
    db.commit()
    prompt = transport.calls[0]["user"]
    assert "2 × Caneca Térmica" in prompt and "já despachado" in prompt and "Nota fiscal: emitida" in prompt
    assert "Maria" not in prompt  # nada de dado pessoal do comprador
    assert "COMPRADOR: Quando chega meu pedido?" in prompt and "LOJA: Obrigado pela compra!" in prompt
    assert action.status == ActionStatus.PENDING and not action.needs_human
    assert action.payload["text"].startswith("Olá! Seu pedido") and conv.status == ConversationStatus.PROPOSED


def test_troca_ou_defeito_vai_para_pessoa_sem_gastar_ia(db):
    conv = conversation(db, text="O produto veio quebrado, quero trocar")
    transport = ai_answer()
    action = process_conversation(db, conv, AIClient(db, transport), "Loja")
    assert action.needs_human and transport.calls == [] and conv.status == ConversationStatus.HUMAN


def test_reclamacao_aberta_vai_para_pessoa(db):
    conv = conversation(db, text="Oi, tudo bem?")
    db.add(Claim(channel=Channel.ML, external_id="C1", order_ref="P1", status="opened", stage="dispute",
                 seller_actions=[], checked_at=utcnow()))
    db.commit()
    transport = ai_answer()
    action = process_conversation(db, conv, AIClient(db, transport), "Loja")
    assert action.needs_human and "mediação" in action.reason and transport.calls == []


def test_sugestao_com_contato_e_assunto_delicado_vao_para_pessoa(db):
    conv = conversation(db, text="Posso falar com vocês por outro lugar?")
    action = process_conversation(db, conv, AIClient(db, ai_answer("Chama no whats 11 99999-9999", "alta")), "Loja")
    assert action.needs_human and action.payload["text"] == ""


def test_rodada_do_agente_e_modo_automatico_respeita_simulacao(db, ml_env):
    connect_ml(db)
    enable(db, mode="automatico")
    conversation(db)
    run = run_postsale_agent(get_sessionmaker(), transport=ai_answer("Olá! Envio até amanhã.", "alta", "prazo_entrega"))
    assert run.status == "ok", run.error
    db.expire_all()
    action = db.scalar(select(Action).where(Action.type == ActionType.SEND_MESSAGE))
    # No automático e com confiança alta é aprovada sozinha — mas no modo simulação nada sai.
    assert action.status == ActionStatus.SIMULATED
    assert db.scalar(select(Conversation)).status == ConversationStatus.SIMULATED


def test_alertas_de_reclamacao(db):
    db.add(Claim(channel=Channel.ML, external_id="C1", order_ref="P9", status="opened", stage="claim",
                 action_responsible="seller", due_date=utcnow() + timedelta(hours=5), affects_reputation="affected",
                 problem="Produto não chegou", seller_actions=[], checked_at=utcnow()))
    db.add(Claim(channel=Channel.ML, external_id="C2", order_ref="P8", status="opened", stage="dispute",
                 action_responsible="mediator", seller_actions=[], checked_at=utcnow()))
    db.commit()
    specs = {s.key: s for s in claim_alerts(db)}
    assert specs["reclamacao:C1"].severity == AlertSeverity.CRITICAL and "Afeta a reputação" in specs["reclamacao:C1"].detail
    assert specs["reclamacao:C2"].severity == AlertSeverity.INFO


# --- Envio (só pelo ActionGateway) --------------------------------------------------

def test_texto_sem_crlf_nem_caracteres_de_controle():
    assert clean_message("Olá\r\nmundo\x85!") == "Olá\nmundo!"


@pytest.fixture
def real_mode(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@respx.mock
def test_modo_real_envia_para_o_agente_de_mensageria(db, ml_env, real_mode):
    connect_ml(db)
    conv = conversation(db, pack="3000001")
    route = respx.post(url__regex=r"https://api\.mercadolibre\.com/messages/packs/3000001/sellers/123456").mock(
        return_value=httpx.Response(202, json={"status": "pending_review"}))
    action = actions.propose(db, agent_slug="posvenda", type=ActionType.SEND_MESSAGE, channel=Channel.ML,
                             target_type="conversa", target_id=conv.id, payload={"text": "Olá! Chega amanhã."})
    actions.approve(db, action, actor="user:1")
    db.commit()
    request = route.calls[0].request
    assert request.url.params["tag"] == "post_sale"
    assert json.loads(request.content) == {"from": {"user_id": SELLER}, "to": {"user_id": MLB_MESSAGING_AGENT_ID},
                                           "text": "Olá! Chega amanhã."}
    assert action.status == ActionStatus.EXECUTED and "revisando" in action.result
    assert conv.status == ConversationStatus.ANSWERED


@respx.mock
def test_conversa_bloqueada_nao_envia(db, ml_env, real_mode):
    connect_ml(db)
    conv = conversation(db)
    conv.remote_status = "blocked"
    route = respx.post(url__regex=r".*/messages/packs/.*").mock(return_value=httpx.Response(200, json={}))
    action = actions.propose(db, agent_slug="posvenda", type=ActionType.SEND_MESSAGE, channel=Channel.ML,
                             target_type="conversa", target_id=conv.id, payload={"text": "Olá!"})
    actions.approve(db, action, actor="user:1")
    assert action.status == ActionStatus.FAILED and route.call_count == 0


# --- Página -------------------------------------------------------------------------

def test_pagina_pos_venda_aprovar_e_ignorar(logged_client, db):
    conv = conversation(db, status=ConversationStatus.PROPOSED)
    other = conversation(db, pack="P2", text="Obrigado!")
    action = actions.propose(db, agent_slug="posvenda", type=ActionType.SEND_MESSAGE, channel=Channel.ML,
                             target_type="conversa", target_id=conv.id, payload={"text": "Chega amanhã!"})
    db.add(Claim(channel=Channel.ML, external_id="C1", order_ref="P9", status="opened", stage="claim",
                 problem="Produto não chegou", action_responsible="seller", seller_actions=[], checked_at=utcnow()))
    db.commit()
    html = logged_client.get("/pos-venda").text
    assert "Quando chega meu pedido?" in html and "Chega amanhã!" in html and "Produto não chegou" in html
    assert "Sua vez de agir" in html
    token = csrf_from(html)
    r = logged_client.post(f"/pos-venda/acao/{action.id}/enviar", data={"csrf_token": token, "text": "Chega\r\namanhã!"})
    assert "Modo simulação" in r.text
    db.expire_all()
    assert db.get(Action, action.id).payload["text"] == "Chega amanhã!"
    assert db.get(Conversation, conv.id).status == ConversationStatus.SIMULATED
    r = logged_client.post(f"/pos-venda/{other.id}/responder", data={"csrf_token": token, "text": "x" * 400})
    assert "até 350 caracteres" in r.text
    assert logged_client.post(f"/pos-venda/{other.id}/responder", data={"text": "Oi"}).status_code == 403


def test_simulacao_mostra_conversas_ficticias(logged_client, db):
    from app.fake_data.seed import clear_demo, seed_demo
    seed_demo(db)
    html = logged_client.get("/pos-venda").text
    assert "chegou quebrado" in html and "chegou amassado" in html
    clear_demo(db)
    db.commit()
    assert db.scalar(select(Conversation)) is None and db.scalar(select(Claim)) is None
