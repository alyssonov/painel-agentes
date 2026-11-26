"""Histórico: antes → depois nas alterações, filtros e permissão."""

from datetime import timedelta

from sqlalchemy import select

from app.core import audit
from app.core.db import utcnow
from app.models import AuditLog
from app.web.routes.history import describe, event_label
from tests.conftest import csrf_from
from tests.test_permissions import _add


def test_changes_so_guarda_o_que_mudou():
    assert audit.changes({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4}) == {
        "antes": {"b": 2, "c": None}, "depois": {"b": 3, "c": 4}}
    assert audit.changes({"senha": "x"}, {"senha": "y"}) == {"antes": {}, "depois": {}}


def test_rotulos_e_descricao():
    assert event_label("ml.conectado") == "Mercado Livre: conectado"
    assert event_label("equipe.pessoa_alterada") == "Alterou uma pessoa"
    changes, extra = describe({"antes": {"ativo": True}, "depois": {"ativo": False}, "acao": 7})
    assert changes == [("Ativo", "sim", "não")] and extra == [("acao", "7")]


def test_alteracao_de_aliquota_mostra_antes_e_depois(logged_client, db):
    token = csrf_from(logged_client.get("/integracoes").text)
    logged_client.post("/integracoes/imposto", data={"csrf_token": token, "tax_rate_pct": "14,25"})
    log = db.scalars(select(AuditLog).where(AuditLog.event == "loja.aliquota_alterada")).one()
    assert log.details["depois"] == {"aliquota_pct": 14.25}
    html = logged_client.get("/historico?tipo=config").text
    assert "Mudou a alíquota de impostos" in html and "14.25" in html and "Alíquota (%)" in html
    assert "Dono" in html  # quem fez


def test_filtros_por_registro_quem_e_periodo(logged_client, db, user):
    audit.record(db, actor="agent:perguntas", event="acao.proposta", target_type="pergunta", target_id="55")
    audit.record(db, actor="system", event="acao.simulada", target_type="pergunta", target_id="99")
    db.commit()
    html = logged_client.get("/historico?registro=pergunta:55").text
    assert "Pergunta: 55" in html and "Sugeriu uma ação" in html and "Ação simulada" not in html
    html = logged_client.get("/historico?quem=agentes").text
    assert "Sugeriu uma ação" in html and "Entrou no painel" not in html
    html = logged_client.get(f"/historico?quem=user:{user.id}&tipo=acessos").text
    assert "Entrou no painel" in html


def test_periodo_corta_registros_antigos(logged_client, db):
    entry = audit.record(db, actor="system", event="precificacao.taxas_alteradas")
    entry.at = utcnow() - timedelta(days=40)
    db.commit()
    assert "Mudou a tabela de taxas" not in logged_client.get("/historico?dias=30").text
    assert "Mudou a tabela de taxas" in logged_client.get("/historico?dias=90").text


def test_historico_exige_permissao(client, db, user):
    from tests.conftest import login
    _add(db, "atende@loja.com", "atendimento")
    login(client, "atende@loja.com")
    assert client.get("/historico").status_code == 403


def test_aprovacao_editada_guarda_sugerido_e_enviado(db):
    from app.core import actions
    from app.models.enums import Channel
    action = actions.propose(db, agent_slug="perguntas", type="responder_pergunta", channel=Channel.ML,
                             target_type="pergunta", target_id=1, payload={"text": "Olá, sim!"})
    db.commit()
    actions.approve(db, action, actor="user:1", edited_payload={"text": "Olá! Sim, serve."})
    db.commit()
    log = db.scalars(select(AuditLog).where(AuditLog.event == "acao.aprovada_editada")).one()
    assert log.details["antes"] == {"text": "Olá, sim!"} and log.details["depois"] == {"text": "Olá! Sim, serve."}
