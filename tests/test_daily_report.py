"""Fase 5: relatório diário por e-mail."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.core.db import get_sessionmaker, utcnow
from app.core.timeutil import local_today, store_tz
from app.fake_data.seed import seed_demo
from app.models import Agent, Alert, Report
from app.notify import email as email_module
from app.notify.email import invalid_recipients, parse_recipients
from app.reports.daily import build_daily, render_html, render_text, run_daily_report, send_daily
from app.services.store_settings import set_setting
from tests.conftest import csrf_from
from tests.olist_helpers import connect_account


class FakeSMTP:
    """Servidor de e-mail falso: guarda o que seria enviado (nenhuma rede)."""
    sent: list = []
    fail_login = False

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        if FakeSMTP.fail_login:
            import smtplib
            raise smtplib.SMTPAuthenticationError(535, b"bad")

    def send_message(self, message):
        FakeSMTP.sent.append(message)


@pytest.fixture
def smtp(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "painel@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "abcd efgh ijkl mnop")
    get_settings.cache_clear()
    FakeSMTP.sent, FakeSMTP.fail_login = [], False
    monkeypatch.setattr(email_module.smtplib, "SMTP", FakeSMTP)
    yield FakeSMTP
    get_settings.cache_clear()


def test_destinatarios():
    assert parse_recipients("a@x.com, B@Y.com.br;c@z.com\nd@w.io") == ["a@x.com", "b@y.com.br", "c@z.com", "d@w.io"]
    assert invalid_recipients(["ok@x.com", "sem-arroba", "a@b"]) == ["sem-arroba", "a@b"]


def test_conteudo_do_relatorio(db):
    seed_demo(db, rng_seed=1)
    content = build_daily(db, local_today())
    assert content["simulacao"] is True and content["pedidos"] > 0
    assert content["mais_vendidos"] and content["alertas"]
    text, html = render_text(content), render_html(content)
    assert "VENDAS:" in text and "O QUE PEDE ATENÇÃO" in text and "SIMULAÇÃO" in text
    assert "Margem real" in html and "<script" not in html


def test_envio_de_teste(db, smtp):
    seed_demo(db, rng_seed=1)
    set_setting(db, "report_recipients", "dono@loja.com, eu@loja.com")
    db.commit()
    report = send_daily(db, local_today(), triggered_by="user:1", test=True)
    assert report.status == "enviado" and report.kind == "teste"
    message = smtp.sent[0]
    assert message["To"] == "dono@loja.com, eu@loja.com"
    assert message["Subject"].startswith("[SIMULAÇÃO] Resumo do dia")
    assert message.get_body(("html",)) is not None  # texto + HTML


def test_sem_destinatario_e_login_recusado(db, smtp):
    report = send_daily(db, local_today())
    assert report.status == "sem_destinatario" and smtp.sent == []
    set_setting(db, "report_recipients", "dono@loja.com")
    smtp.fail_login = True
    report = send_daily(db, local_today())
    assert report.status == "falhou" and "senha de app" in report.error


def test_sem_email_configurado(db, monkeypatch):
    monkeypatch.setenv("SMTP_USER", "")
    get_settings.cache_clear()
    set_setting(db, "report_recipients", "dono@loja.com")
    report = send_daily(db, local_today())
    assert report.status == "falhou" and "SMTP_USER" in report.error
    get_settings.cache_clear()


def _enable_report(db, hour=19):
    from app.agents.registry import ensure_agents
    ensure_agents(db)
    agent = db.scalar(select(Agent).where(Agent.slug == "relatorio"))
    agent.enabled, agent.config = True, {"hora_envio": hour}
    db.commit()


def test_envio_automatico_uma_vez_por_dia(db, smtp):
    connect_account(db)
    _enable_report(db)
    set_setting(db, "report_recipients", "dono@loja.com")
    db.commit()
    today = local_today()
    before = datetime.combine(today, datetime.min.time(), tzinfo=store_tz()).replace(hour=18, minute=50)
    after = before.replace(hour=19, minute=5)
    sm = get_sessionmaker()
    assert run_daily_report(sm, now_local=before) is None  # ainda não é hora
    run = run_daily_report(sm, now_local=after)
    assert run.status == "ok" and run.summary.startswith("Enviado às")
    assert run_daily_report(sm, now_local=after.replace(hour=21)) is None  # não repete
    assert len(smtp.sent) == 1
    assert db.scalar(select(Report).where(Report.kind == "diario")).status == "enviado"


def test_falha_vira_alerta_e_tenta_de_novo(db, smtp):
    connect_account(db)
    _enable_report(db)
    set_setting(db, "report_recipients", "dono@loja.com")
    db.commit()
    smtp.fail_login = True
    now = datetime.now(store_tz()).replace(hour=19, minute=10)
    sm = get_sessionmaker()
    run_daily_report(sm, now_local=now)
    alert = db.scalar(select(Alert).where(Alert.source == "relatorio"))
    assert alert is not None and alert.status == "aberto"
    assert run_daily_report(sm, now_local=now) is None  # espera 30 min antes de tentar de novo
    report = db.scalar(select(Report).where(Report.kind == "diario"))
    report.updated_at = utcnow() - timedelta(minutes=31)
    db.commit()
    smtp.fail_login = False
    run_daily_report(sm, now_local=now)
    db.expire_all()
    assert db.scalar(select(Report).where(Report.kind == "diario")).status == "enviado"
    assert db.scalar(select(Alert).where(Alert.source == "relatorio")).status == "resolvido"


def test_pagina_de_relatorios(logged_client, db, smtp):
    html = logged_client.get("/relatorios").text
    assert "Quem recebe" in html and "Prévia de hoje" in html
    previa = logged_client.get("/relatorios/previa")
    assert "Resumo de hoje" in previa.text
    assert "frame-ancestors 'self'" in previa.headers["content-security-policy"]
    token = csrf_from(html)
    r = logged_client.post("/relatorios/destinatarios", data={"csrf_token": token, "recipients": "x@y.com, ruim"})
    assert "E-mail inválido: ruim" in r.text
    logged_client.post("/relatorios/destinatarios", data={"csrf_token": token, "recipients": "dono@loja.com"})
    r = logged_client.post("/relatorios/enviar-agora", data={"csrf_token": token})
    assert "Relatório de teste enviado para dono@loja.com" in r.text
    assert "Teste" in r.text and "Enviado" in r.text
