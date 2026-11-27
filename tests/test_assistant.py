"""Assistente de IA: loop de consultas (só leitura), permissões, custo, telas e planilhas. Sem rede."""

import json

import pytest
from sqlalchemy import select

from app.ai import assistant
from app.ai.assistant import ChatResult, ask, build_system
from app.ai.assistant_tools import run_tool
from app.ai.client import BudgetExceeded
from app.core.db import utcnow
from app.models import AiUsage, AuditLog, Product, StockSnapshot
from app.models.assistant import AssistantConversation, AssistantMessage
from app.web.markdown_lite import parse_tables, render
from tests.conftest import csrf_from, login
from tests.test_permissions import _add


class Script:
    """IA falsa: devolve as respostas na ordem e guarda o que recebeu."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, model, effort, system, tools, messages, max_tokens):
        self.calls.append({"model": model, "system": system, "tools": tools, "messages": json.loads(json.dumps(messages))})
        return self.replies.pop(0)


def tool_call(name, inp, call_id="t1"):
    return ChatResult(content=[{"type": "tool_use", "id": call_id, "name": name, "input": inp}],
                      stop_reason="tool_use", input_tokens=1000, output_tokens=50)


def text(t):
    return ChatResult(content=[{"type": "text", "text": t}], stop_reason="end_turn", input_tokens=1200,
                      output_tokens=200, cache_read_tokens=900)


def _product(db):
    p = Product(sku="PROT-BIZ", name="Protetor Escapamento Biz", cost_cents=1500, price_cents=4990, brand="Pro Tork")
    db.add(p)
    db.flush()
    db.add(StockSnapshot(product_id=p.id, quantity=7, reserved=0, captured_at=utcnow()))
    db.commit()
    return p


def test_consulta_e_responde_com_os_dados(db, user):
    _product(db)
    script = Script(tool_call("buscar_produtos", {"busca": "protetor biz"}),
                    text("Achei 1 produto:\n\n| Produto | Estoque |\n|---|---|\n| Protetor Escapamento Biz | 7 |"))
    answer = ask(db, user, [], "tem protetor da biz?", transport=script)
    assert "Protetor Escapamento Biz" in answer.text and answer.tools_used == ["buscar_produtos"]
    # 2ª chamada recebeu o resultado da consulta (só leitura), ligado ao pedido da IA
    result = script.calls[1]["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "t1"
    data = json.loads(result["content"])
    assert data["produtos"][0]["sku"] == "PROT-BIZ" and data["produtos"][0]["custo_olist"] == "R$ 15,00"
    assert data["produtos"][0]["marca"] == "Pro Tork"
    # custo registrado a cada chamada, no modelo barato configurado
    usages = list(db.scalars(select(AiUsage).where(AiUsage.task == "assistente")))
    assert len(usages) == 2 and usages[0].model == "claude-haiku-5-5" and answer.cost_brl_millicents > 0
    # instruções fixas vão com cache de prompt; a data de hoje vai na pergunta (fora do cache)
    assert script.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Hoje é" in script.calls[0]["messages"][-1]["content"]


def test_sem_financeiro_nao_ve_custo_nem_dre(db, user):
    _product(db)
    op = _add(db, "op@loja.com", "operador")
    out, err = run_tool(db, op, "buscar_produtos", {"busca": "biz"})
    assert not err and "custo" not in out
    out, err = run_tool(db, op, "dre_do_mes", {"mes": "2026-11"})
    assert err and "não tem permissão" in out
    out, err = run_tool(db, op, "resumo_vendas", {"dias": 7})
    assert not err and "lucro_real" not in out
    # mapa de telas só com o que a pessoa pode abrir
    system = build_system(op)[0]["text"]
    assert "/pedidos" in system and "/financeiro/dre" not in system and "/equipe" not in system
    assert "/financeiro/dre" in build_system(user)[0]["text"]
    out, err = run_tool(db, op, "inventada", {})
    assert err


def test_historico_curto_e_so_texto(db, user):
    history = [("user", f"pergunta {i}") if i % 2 == 0 else ("assistant", f"resposta {i}") for i in range(10)]
    script = Script(text("ok"))
    ask(db, user, history, "e agora?", transport=script)
    sent = script.calls[0]["messages"]
    assert sent[0]["role"] == "user" and len(sent) <= assistant.HISTORY_TURNS + 1
    assert all(isinstance(m["content"], str) for m in sent)


def test_teto_diario_recusa_e_limite_de_rodadas(db, user, monkeypatch):
    db.add(AiUsage(agent_slug="x", task="x", model="claude-haiku-5-5", cost_brl_millicents=10_000_000))
    db.commit()
    with pytest.raises(BudgetExceeded):
        ask(db, user, [], "oi", transport=Script(text("nunca")))
    db.query(AiUsage).delete()
    db.commit()
    refused = ChatResult(content=[], stop_reason="refusal")
    assert "Prefiro não responder" in ask(db, user, [], "x", transport=Script(refused)).text
    monkeypatch.setattr(assistant, "MAX_ROUNDS", 2)
    loop = Script(tool_call("pendencias", {}), tool_call("pendencias", {}, "t2"))
    assert "consultas demais" in ask(db, user, [], "x", transport=loop).text


def test_markdown_seguro_e_tabelas():
    html = render("Oi <img src=x onerror=alert(1)> [ir](/pedidos) [mal](javascript:alert(1)) **ok**")
    assert "<img" not in html and "&lt;img" in html and 'href="/pedidos"' in html and "javascript:" not in html
    assert "<strong>ok</strong>" in html
    tables = parse_tables("| A | B |\n|---|---|\n| 1 | 2 |\n\ntexto\n\n| C |\n|---|\n| 3 |")
    assert tables == [{"header": ["A", "B"], "rows": [["1", "2"]]}, {"header": ["C"], "rows": [["3"]]}]


def test_tela_conversa_e_planilha(logged_client, db, user, monkeypatch):
    replies = [text("Suas vendas:\n\n| Marketplace | Vendas |\n|---|---|\n| Mercado Livre | R$ 100,00 |"),
               text("Continuando a conversa.")]
    monkeypatch.setattr(assistant, "anthropic_chat", lambda *a: replies.pop(0))
    from app.config import get_settings
    monkeypatch.setenv("ANTHROPIC_API_KEY", "teste-sem-rede")  # a "IA" é a de mentira acima
    get_settings.cache_clear()
    html = logged_client.get("/assistente").text
    assert "Nova conversa" in html and "Quanto vendi esta semana" in html
    token = csrf_from(html)
    r = logged_client.post("/assistente/perguntar", data={"csrf_token": token, "pergunta": "quanto vendi?"},
                           headers={"X-Requested-With": "fetch"})
    data = r.json()
    assert r.status_code == 200 and "<table" in data["resposta"]["html"] and "Baixar esta tabela" in data["resposta"]["html"]
    conv = db.scalar(select(AssistantConversation))
    assert conv.title == "quanto vendi?" and conv.user_id == user.id
    reply = db.scalar(select(AssistantMessage).where(AssistantMessage.role == "assistant"))
    csv = logged_client.get(f"/assistente/mensagem/{reply.id}/tabela/0.csv")
    assert csv.status_code == 200 and "Mercado Livre;R$ 100,00" in csv.text
    r = logged_client.post("/assistente/perguntar", data={"csrf_token": token, "pergunta": "e ontem?",
                                                          "conversa": str(conv.id)}, headers={"X-Requested-With": "fetch"})
    assert r.json()["conversa"] == conv.id
    assert db.scalars(select(AuditLog).where(AuditLog.event == "assistente.pergunta")).first() is not None
    assert "quanto vendi?" in logged_client.get(f"/assistente?c={conv.id}").text
    get_settings.cache_clear()


def test_cada_pessoa_ve_so_as_suas_conversas(client, db, user):
    conv = AssistantConversation(user_id=user.id, title="segredo do dono")
    db.add(conv)
    db.flush()
    msg = AssistantMessage(conversation_id=conv.id, role="assistant", text="| A |\n|---|\n| 1 |",
                           tables=[{"header": ["A"], "rows": [["1"]]}])
    db.add(msg)
    db.commit()
    _add(db, "at@loja.com", "atendimento")
    login(client, "at@loja.com")
    assert "segredo do dono" not in client.get(f"/assistente?c={conv.id}").text
    assert client.get(f"/assistente/mensagem/{msg.id}/tabela/0.csv").status_code == 404
    assert 'data-asst-abrir' in client.get("/perguntas").text  # botão flutuante para quem pode usar


def test_sem_chave_avisa(logged_client, db):
    token = csrf_from(logged_client.get("/assistente").text)
    r = logged_client.post("/assistente/perguntar", data={"csrf_token": token, "pergunta": "oi"},
                           headers={"X-Requested-With": "fetch"})
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["erro"]


def test_consultas_novas_publicidade_contas_e_automacoes(db, user):
    """Assistente conhece Mercado Ads, contas conectadas e automações — só leitura e com permissões."""
    from datetime import timedelta

    from app.core.timeutil import local_today
    from app.models import AdCampaign, AdDaily, AdGroup, IntegrationAccount
    db.add(IntegrationAccount(provider="ml", account_name="LOJA", is_demo=False, access_token_enc="x",
                              status="ok", extra={"ads_status": "ok"}))
    db.add(AdCampaign(campaign_id="1", name="Pastilhas", status="active", cost_cents=10000, revenue_cents=60000,
                      sales=5))
    db.add(AdDaily(campaign_id="1", day=local_today() - timedelta(days=1), cost_cents=10000, revenue_cents=60000))
    db.add(AdGroup(ad_group_id="9", title="Retentor CG", cost_cents=4500, revenue_cents=0, sales=0, clicks=30))
    db.commit()
    out, err = run_tool(db, user, "publicidade", {"dias": 7})
    data = json.loads(out)
    assert not err and data["investimento"] == "R$ 100,00" and data["retorno_roas"] == "6,0x"
    assert data["anuncios_gastando_sem_vender"][0]["anuncio"] == "Retentor CG"
    out, err = run_tool(db, user, "contas_conectadas", {})
    assert not err and "Mercado Livre" in out and "access_token" not in out and '"x"' not in out
    out, err = run_tool(db, user, "automacoes", {})
    assert not err and "Envio atrasado" in out
    # sem permissão: publicidade é do financeiro; automações de quem configura agentes
    at = _add(db, "at@loja.com", "atendimento")
    assert run_tool(db, at, "publicidade", {})[1] and run_tool(db, at, "automacoes", {})[1]
    assert not run_tool(db, at, "contas_conectadas", {})[1]
    # mapa de telas: as novas aparecem só para quem pode abrir
    assert "/publicidade" in build_system(user)[0]["text"] and "/automacoes" in build_system(user)[0]["text"]
    assert "/publicidade" not in build_system(at)[0]["text"]
