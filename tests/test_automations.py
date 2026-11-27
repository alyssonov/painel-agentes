"""Automações: avisos por e-mail a partir dos alertas (sem repetir, respeitando o silêncio)."""

from datetime import datetime, timedelta

from sqlalchemy import select

from app.automations.runner import QUIET_KEY, RECIPIENTS_KEY, ensure_rows, is_quiet, run_automations
from app.core.db import get_sessionmaker
from app.models import Alert, AuditLog, AutomationDelivery, Question, Report
from app.models.enums import AlertSeverity, AlertStatus, QuestionStatus
from app.notify.email import EmailError
from app.services.store_settings import set_setting

NOON = datetime(2026, 11, 27, 15, 0)  # 12h em Brasília (UTC-3)


class Outbox:
    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    def __call__(self, to, subject, text, html=None):
        if self.fail:
            raise EmailError(self.fail)
        self.sent.append((to, subject, text))


def _setup(db, *slugs, recipients="dono@loja.com", enabled_at=NOON - timedelta(hours=1)):
    rows = ensure_rows(db)
    for slug in slugs:
        rows[slug].enabled, rows[slug].enabled_at = True, enabled_at
    set_setting(db, RECIPIENTS_KEY, recipients)
    db.commit()
    return rows


def _alert(db, source, severity, title, created=NOON - timedelta(minutes=10)):
    a = Alert(source=source, severity=severity, title=title, target_id=title, created_at=created)
    db.add(a)
    db.commit()
    return a


def test_manda_um_email_com_os_alertas_novos_e_nao_repete(db):
    _setup(db, "estoque", "urgente")
    _alert(db, "estoque", AlertSeverity.WARNING, "Pastilha acabando")
    _alert(db, "despacho", AlertSeverity.CRITICAL, "Pedido 123 atrasado")
    _alert(db, "estoque", AlertSeverity.WARNING, "Antigo", created=NOON - timedelta(days=2))  # antes de ligar
    _alert(db, "margem", AlertSeverity.WARNING, "Venda no prejuízo")  # automação desligada
    out = Outbox()
    result = run_automations(get_sessionmaker(), now=NOON, sender=out)
    assert result.sent == 2 and len(out.sent) == 1
    to, subject, text = out.sent[0]
    assert to == ["dono@loja.com"] and "2 avisos" in subject
    assert "Pastilha acabando" in text and "Pedido 123 atrasado" in text
    assert "Antigo" not in text and "prejuízo" not in text
    # 2ª rodada: nada novo, nada enviado
    assert run_automations(get_sessionmaker(), now=NOON, sender=out).sent == 0 and len(out.sent) == 1
    # registrado em reports e no histórico
    assert db.scalar(select(Report).where(Report.kind == "avisos")).content == {"avisos": 2}
    assert db.scalar(select(AuditLog).where(AuditLog.event == "automacao.enviada")) is not None


def test_horario_silencioso_segura_e_manda_depois(db):
    _setup(db, "urgente", enabled_at=datetime(2026, 11, 26))
    set_setting(db, QUIET_KEY, {"ativo": True, "de": 22, "ate": 7})
    db.commit()
    night = datetime(2026, 11, 27, 4, 0)  # 1h da manhã em Brasília
    _alert(db, "nfe", AlertSeverity.CRITICAL, "Pedido sem nota", created=night - timedelta(minutes=5))
    out = Outbox()
    assert run_automations(get_sessionmaker(), now=night, sender=out).waiting_quiet and out.sent == []
    morning = datetime(2026, 11, 27, 10, 30)  # 7h30 em Brasília
    assert run_automations(get_sessionmaker(), now=morning, sender=out).sent == 1
    assert is_quiet({"ativo": True, "de": 22, "ate": 7}, datetime(2026, 11, 28, 1, 30))  # 22h30
    assert not is_quiet({"ativo": False, "de": 22, "ate": 7}, night)


def test_falha_no_email_nao_marca_como_avisado(db):
    rows = _setup(db, "urgente")
    _alert(db, "nfe", AlertSeverity.CRITICAL, "Pedido sem nota")
    result = run_automations(get_sessionmaker(), now=NOON, sender=Outbox(fail="SMTP fora do ar"))
    assert result.error == "SMTP fora do ar" and db.scalar(select(AutomationDelivery)) is None
    db.refresh(rows["urgente"])
    assert rows["urgente"].last_error == "SMTP fora do ar"
    out = Outbox()
    assert run_automations(get_sessionmaker(), now=NOON, sender=out).sent == 1  # tenta de novo depois


def test_alertas_da_simulacao_nunca_saem(db):
    _setup(db, "urgente")
    db.add(Alert(source="nfe", severity=AlertSeverity.CRITICAL, title="fictício", is_demo=True, created_at=NOON))
    db.commit()
    out = Outbox()
    assert run_automations(get_sessionmaker(), now=NOON, sender=out).sent == 0 and out.sent == []


def test_atrasos_com_opcao_de_avisar_o_que_vence(db):
    rows = _setup(db, "atrasos")
    _alert(db, "despacho", AlertSeverity.WARNING, "Vence às 15h")
    out = Outbox()
    assert run_automations(get_sessionmaker(), now=NOON, sender=out).sent == 0  # só atrasado, por padrão
    rows["atrasos"].params = {"incluir_vencendo": True}
    db.commit()
    assert run_automations(get_sessionmaker(), now=NOON, sender=out).sent == 1


def test_perguntas_esperando_viram_aviso(db):
    _setup(db, "perguntas", enabled_at=datetime(2026, 1, 1))
    db.add(Question(channel="ml", external_id="q1", listing_title="Pastilha CG", text="Serve na Titan 2015?",
                    asked_at=NOON - timedelta(hours=3), status=QuestionStatus.NEW))
    db.add(Question(channel="ml", external_id="q2", listing_title="Retentor", text="Tem pronta entrega?",
                    asked_at=NOON - timedelta(minutes=30), status=QuestionStatus.NEW))
    db.commit()
    out = Outbox()
    result = run_automations(get_sessionmaker(), now=NOON, sender=out)
    assert result.sent == 1 and "Titan 2015" in out.sent[0][2] and "pronta entrega" not in out.sent[0][2]
    # respondida: o alerta some sozinho
    db.scalar(select(Question).where(Question.external_id == "q1")).status = QuestionStatus.ANSWERED
    db.commit()
    run_automations(get_sessionmaker(), now=NOON, sender=out)
    assert db.scalar(select(Alert).where(Alert.source == "perguntas_espera")).status == AlertStatus.RESOLVED


def test_sem_destinatario_avisa_o_motivo(db):
    _setup(db, "urgente", recipients="")
    _alert(db, "nfe", AlertSeverity.CRITICAL, "Pedido sem nota")
    result = run_automations(get_sessionmaker(), now=NOON, sender=Outbox())
    assert result.error and "Ninguém cadastrado" in result.error


def test_tela_ligar_e_configurar(logged_client, db):
    from tests.conftest import csrf_from
    page = logged_client.get("/automacoes")
    assert page.status_code == 200 and "Envio atrasado" in page.text and "style=" not in page.text
    csrf = csrf_from(page.text)
    logged_client.post("/automacoes/estoque/ativo", data={"csrf_token": csrf, "enabled": "1"})
    logged_client.post("/automacoes/perguntas/config", data={"csrf_token": csrf, "horas": "500"})
    db.expire_all()
    rows = ensure_rows(db)
    assert rows["estoque"].enabled and rows["estoque"].enabled_at is not None
    assert rows["perguntas"].params["horas"] == 72  # respeita o limite máximo
    logged_client.post("/automacoes/geral", data={"csrf_token": csrf, "recipients": "nao-e-email"})
    assert "nao-e-email" not in logged_client.get("/automacoes").text.split("Quem recebe")[1][:600]


def test_quem_nao_configura_agentes_nao_abre(client, db, user):
    from tests.conftest import login
    from tests.test_permissions import _add
    _add(db, "atend@loja.com", "atendimento")
    login(client, "atend@loja.com")
    assert client.get("/automacoes", follow_redirects=False).status_code in (303, 403)
