"""Agente 8 — Relatório diário (sem IA: texto montado a partir dos números).

Todo dia, a partir do horário configurado (padrão 19h), envia por e-mail um
resumo do dia em linguagem simples. Se o computador estiver desligado nesse
horário, envia assim que o painel ligar (até o fim do dia). Nunca envia o
mesmo relatório duas vezes (tabela `reports`, um por dia).

É uma mensagem INTERNA (para a loja), não para clientes: por isso é enviada
também no modo simulação.
"""

import html
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.base import run_agent
from app.agents.config import agent_rules, has_real_data
from app.ai.client import spent_today_brl
from app.config import get_settings
from app.core import audit
from app.core.alerts import AlertSpec, sync_alerts
from app.core.db import utcnow
from app.core.money import format_brl, format_pct
from app.core.timeutil import local_day_bounds_utc, local_today, store_tz, to_local
from app.models import Agent, AgentRun, Alert, Order, Question, Report
from app.models.enums import CHANNEL_LABELS, AlertSeverity, AlertStatus, Channel, KanbanStatus, QuestionStatus
from app.notify.email import EmailError, parse_recipients, send_email
from app.services.margin_report import margin_report
from app.services.store_settings import get_setting

SLUG = "relatorio"
KIND = "diario"
KIND_TEST = "teste"
RETRY_AFTER = timedelta(minutes=30)
WEEKDAYS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
SEVERITY_ICON = {AlertSeverity.CRITICAL: "🔴", AlertSeverity.WARNING: "🟡", AlertSeverity.INFO: "🔵"}
SEVERITY_ORDER = {AlertSeverity.CRITICAL: 0, AlertSeverity.WARNING: 1, AlertSeverity.INFO: 2}


# --- Conteúdo -----------------------------------------------------------------------

def build_daily(db: Session, day: date, *, now: datetime | None = None) -> dict:
    """Números do dia, já formatados para leitura."""
    demo = not has_real_data(db)
    now = now or utcnow()
    start, end = local_day_bounds_utc(day)
    end = min(end, now) if day == local_today() else end
    tax = float(get_setting(db, "tax_rate_pct") or 0)
    report = margin_report(db, start, end, tax, demo=demo)

    # Comparação justa: ontem até o mesmo horário.
    y_start = start - timedelta(days=1)
    y_end = y_start + (end - start)
    yesterday = db.scalar(select(func.coalesce(func.sum(Order.items_total_cents), 0)).where(
        Order.ordered_at >= y_start, Order.ordered_at < y_end, Order.is_demo.is_(demo),
        Order.kanban_status != KanbanStatus.CANCELLED)) or 0

    channels = []
    for channel, count, total in db.execute(
            select(Order.channel, func.count(), func.coalesce(func.sum(Order.items_total_cents), 0))
            .where(Order.ordered_at >= start, Order.ordered_at < end, Order.is_demo.is_(demo),
                   Order.kanban_status != KanbanStatus.CANCELLED)
            .group_by(Order.channel).order_by(func.sum(Order.items_total_cents).desc())):
        label = CHANNEL_LABELS.get(Channel(channel), channel) if channel in Channel._value2member_map_ else channel
        channels.append(f"{label}: {count} pedido(s) · {format_brl(total)}")

    top = sorted(report.products, key=lambda p: p.units, reverse=True)[:5]
    losses = [p.name for p in report.products if p.profit < 0 and not p.missing_cost]

    answered = db.scalar(select(func.count()).select_from(Question).where(
        Question.answered_at >= start, Question.answered_at < end, Question.is_demo.is_(demo),
        Question.status.in_([QuestionStatus.ANSWERED, QuestionStatus.SIMULATED]))) or 0
    waiting = db.scalar(select(func.count()).select_from(Question).where(
        Question.is_demo.is_(demo),
        Question.status.in_([QuestionStatus.NEW, QuestionStatus.PROPOSED, QuestionStatus.HUMAN]))) or 0

    alerts = sorted(db.scalars(select(Alert).where(Alert.status == AlertStatus.OPEN, Alert.is_demo.is_(demo))),
                    key=lambda a: (SEVERITY_ORDER.get(AlertSeverity(a.severity), 9), -a.id))

    latest = select(func.max(AgentRun.id)).group_by(AgentRun.agent_id)
    runs = {r.agent_id: r for r in db.scalars(select(AgentRun).where(AgentRun.id.in_(latest),
                                                                      AgentRun.started_at >= start))}
    agents = [f"{a.name}: {runs[a.id].summary}" for a in db.scalars(select(Agent).order_by(Agent.position))
              if a.enabled and a.slug != SLUG and a.id in runs and runs[a.id].summary]

    diff = ((report.totals.revenue - yesterday) / yesterday * 100) if yesterday else None
    return {
        "dia": day.isoformat(),
        "titulo": f"Resumo de {'hoje' if day == local_today() else 'dia'} — {WEEKDAYS[day.weekday()]}, {day:%d/%m}",
        "simulacao": demo,
        "pedidos": report.totals.orders,
        "faturamento": format_brl(report.totals.revenue),
        "comparacao": (f"{'▲' if diff >= 0 else '▼'} {format_pct(abs(diff), 0)} em relação a ontem no mesmo horário"
                       if diff is not None else "Sem vendas ontem no mesmo horário"),
        "canais": channels,
        "margem": format_pct(report.totals.margin_pct),
        "lucro": format_brl(report.totals.profit),
        "prejuizos": losses[:5],
        "sem_custo": report.products_missing_cost,
        "mais_vendidos": [f"{p.name} ({p.units})" for p in top],
        "perguntas_respondidas": answered,
        "perguntas_aguardando": waiting,
        "alertas": [{"icone": SEVERITY_ICON.get(AlertSeverity(a.severity), "•"), "texto": a.title} for a in alerts[:8]],
        "alertas_total": len(alerts),
        "agentes": agents,
        "gasto_ia": format_brl(round(spent_today_brl(db) * 100)),
    }


def render_text(c: dict) -> str:
    lines = [("[SIMULAÇÃO — dados de exemplo] " if c["simulacao"] else "") + c["titulo"], ""]
    lines += [f"VENDAS: {c['pedidos']} pedido(s) · {c['faturamento']}", f"  {c['comparacao']}"]
    lines += [f"  {line}" for line in c["canais"]]
    lines += ["", f"MARGEM REAL: {c['margem']} · lucro de {c['lucro']}"]
    if c["prejuizos"]:
        lines.append(f"  Atenção: venda com prejuízo em {', '.join(c['prejuizos'])}")
    if c["sem_custo"]:
        lines.append(f"  {c['sem_custo']} produto(s) vendido(s) sem custo no Olist (a margem fica errada)")
    if c["mais_vendidos"]:
        lines += ["", "MAIS VENDIDOS: " + " · ".join(c["mais_vendidos"])]
    lines += ["", f"PERGUNTAS: {c['perguntas_respondidas']} respondida(s) · {c['perguntas_aguardando']} esperando você"]
    lines += ["", "O QUE PEDE ATENÇÃO:"]
    lines += [f"  {a['icone']} {a['texto']}" for a in c["alertas"]] or ["  Nada pendente. Tudo em ordem."]
    if c["alertas_total"] > len(c["alertas"]):
        lines.append(f"  … e mais {c['alertas_total'] - len(c['alertas'])} no painel")
    if c["agentes"]:
        lines += ["", "O QUE OS AGENTES FIZERAM:"] + [f"  {a}" for a in c["agentes"]]
    lines += ["", f"Gasto com IA hoje: {c['gasto_ia']}", "", "Gerente do Ano — para detalhes, abra o painel."]
    return "\n".join(lines)


def render_html(c: dict) -> str:
    """HTML do e-mail (estilos inline: é o que os programas de e-mail aceitam)."""
    e = html.escape
    navy, muted, red, amber = "#1F3243", "#5B6573", "#C0362C", "#8F5600"

    def section(title: str, body: str) -> str:
        return (f'<tr><td style="padding:14px 24px;border-top:1px solid #E0E3E8">'
                f'<div style="font-size:12px;font-weight:700;letter-spacing:.04em;color:{muted};text-transform:uppercase;'
                f'margin-bottom:6px">{e(title)}</div>{body}</td></tr>')

    def p(text: str, color: str = "#1E2B3A", size: int = 14, bold: bool = False) -> str:
        weight = "700" if bold else "400"
        return f'<div style="font-size:{size}px;color:{color};font-weight:{weight};line-height:1.5">{text}</div>'

    sales = p(f"{c['pedidos']} pedido(s) · <b>{e(c['faturamento'])}</b>", size=18) + p(e(c["comparacao"]), muted, 13)
    sales += "".join(p(e(line), muted, 13) for line in c["canais"])
    margin = p(f"<b>{e(c['margem'])}</b> · lucro de {e(c['lucro'])}", size=16)
    if c["prejuizos"]:
        margin += p("⚠ Venda com prejuízo em " + e(", ".join(c["prejuizos"])), red, 13, True)
    if c["sem_custo"]:
        margin += p(f"{c['sem_custo']} produto(s) vendido(s) sem custo no Olist", amber, 13)
    alerts = "".join(p(f"{a['icone']} {e(a['texto'])}") for a in c["alertas"]) or p("✓ Nada pendente. Tudo em ordem.", "#17733F")
    if c["alertas_total"] > len(c["alertas"]):
        alerts += p(f"… e mais {c['alertas_total'] - len(c['alertas'])} no painel", muted, 13)
    rows = [section("Vendas", sales), section("Margem real", margin)]
    if c["mais_vendidos"]:
        rows.append(section("Mais vendidos", p(e(" · ".join(c["mais_vendidos"])))))
    rows.append(section("Perguntas", p(f"{c['perguntas_respondidas']} respondida(s) · "
                                       f"<b>{c['perguntas_aguardando']}</b> esperando você")))
    rows.append(section("O que pede atenção", alerts))
    if c["agentes"]:
        rows.append(section("O que os agentes fizeram", "".join(p(e(a), size=13) for a in c["agentes"])))
    banner = (f'<tr><td style="background:#FFE9A8;color:#4A3800;font-size:12px;padding:8px 24px">'
              f'<b>SIMULAÇÃO</b> — dados de exemplo</td></tr>') if c["simulacao"] else ""
    return (
        f'<!doctype html><html><body style="margin:0;background:#EEF2F6;font-family:Segoe UI,Arial,sans-serif">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#EEF2F6;padding:24px 0">'
        f'<tr><td align="center"><table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        f'style="max-width:600px;width:100%;background:#fff;border-radius:16px;overflow:hidden">'
        f'<tr><td style="background:{navy};color:#fff;padding:20px 24px"><div style="font-size:13px;opacity:.75">'
        f'🏆 Gerente do Ano</div><div style="font-size:20px;font-weight:700">{e(c["titulo"])}</div></td></tr>'
        f'{banner}{"".join(rows)}'
        f'<tr><td style="padding:14px 24px;border-top:1px solid #E0E3E8;font-size:12px;color:{muted}">'
        f'Gasto com IA hoje: {e(c["gasto_ia"])} · Para detalhes, abra o painel.</td></tr>'
        f'</table></td></tr></table></body></html>'
    )


# --- Envio --------------------------------------------------------------------------------

def recipients(db: Session) -> list[str]:
    return parse_recipients(get_setting(db, "report_recipients"))


def send_daily(db: Session, day: date, *, triggered_by: str = "system", test: bool = False) -> Report:
    """Monta e envia. test=True: envio manual de teste (não conta como o envio do dia)."""
    kind = KIND_TEST if test else KIND
    report = db.scalar(select(Report).where(Report.kind == kind, Report.period_start == day))
    if report is None:
        report = Report(kind=kind, period_start=day, period_end=day, status="pendente")
        db.add(report)
    content = build_daily(db, day)
    report.content, report.triggered_by = content, triggered_by
    to = recipients(db)
    if not to:
        report.status, report.error, report.sent_to = "sem_destinatario", "Nenhum destinatário cadastrado.", None
    else:
        subject = ("[SIMULAÇÃO] " if content["simulacao"] else "") + \
                  f"Resumo do dia {day:%d/%m}: {content['faturamento']} em {content['pedidos']} pedido(s)"
        try:
            send_email(to, subject, render_text(content), render_html(content))
            report.status, report.error, report.sent_at = "enviado", None, utcnow()
        except EmailError as exc:
            report.status, report.error = "falhou", str(exc)
        report.sent_to = ", ".join(to)
    audit.record(db, actor=triggered_by, event=f"relatorio.{report.status}", target_type="relatorio",
                 target_id=f"{kind}:{day.isoformat()}", details={"para": report.sent_to, "erro": report.error})
    db.commit()
    return report


def due_today(db: Session, send_hour: int, now_local: datetime) -> bool:
    """Já passou do horário e o relatório de hoje ainda não saiu (respeitando novas tentativas)?"""
    if now_local.hour < send_hour:
        return False
    report = db.scalar(select(Report).where(Report.kind == KIND, Report.period_start == now_local.date()))
    if report is None:
        return True
    if report.status == "enviado":
        return False
    if report.status == "sem_destinatario":
        return bool(recipients(db))  # alguém foi cadastrado depois: envia
    return utcnow() - report.updated_at >= RETRY_AFTER  # falhou: tenta de novo a cada 30 min


def run_daily_report(session_factory: sessionmaker[Session], *, now_local: datetime | None = None):
    """Chamado pelo worker a cada 10 minutos."""
    now_local = now_local or datetime.now(store_tz())
    with session_factory() as db:
        if not has_real_data(db):
            return None
        agent = db.scalar(select(Agent).where(Agent.slug == SLUG))
        if agent is None or not agent.enabled:
            return None
        if not due_today(db, int(agent_rules(agent)["hora_envio"]), now_local):
            return None

        def work(db: Session, _agent) -> tuple[int, str]:
            report = send_daily(db, now_local.date())
            # Falha no envio vira alerta (e some sozinho quando der certo).
            sync_alerts(db, SLUG, [AlertSpec("envio", AlertSeverity.CRITICAL, "O relatório diário não foi enviado",
                                             report.error, "relatorio")] if report.status == "falhou" else [])
            db.commit()
            when = to_local(report.sent_at).strftime("%H:%M") if report.sent_at else None
            if report.status == "enviado":
                return 1, f"Enviado às {when} para {len(parse_recipients(report.sent_to))} pessoa(s)"
            if report.status == "sem_destinatario":
                return 0, "Cadastre quem recebe o relatório em Relatórios"
            return 0, f"Não enviado: {report.error}"

        return run_agent(db, SLUG, work)


def is_configured() -> bool:
    return get_settings().email_configured
