"""Despesas da empresa e DRE mensal."""

from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.core.timeutil import local_today
from app.models import AuditLog, Order, OrderItem, Product
from app.models.enums import Channel, KanbanStatus
from app.models.expense import Expense
from app.services.dre import add_months, month_dre, month_start, parse_month
from app.services.store_settings import set_setting
from tests.conftest import csrf_from, login
from tests.test_permissions import _add


def _sale(db, when: datetime, total=10000, fee=1200, ship=1500, cost=4000, net=None, cancelled=False):
    p = Product(sku=f"P{when.timestamp()}", name="Peça", cost_cents=cost)
    db.add(p)
    db.flush()
    o = Order(channel=Channel.ML, external_id=f"E{when.timestamp()}", customer_short_name="A.",
              items_total_cents=total, marketplace_fee_cents=fee, shipping_cost_cents=ship, ordered_at=when,
              net_received_cents=net, kanban_status=KanbanStatus.CANCELLED if cancelled else KanbanStatus.SHIPPED)
    o.items.append(OrderItem(sku=p.sku, title="Peça", quantity=1, unit_price_cents=total, product_id=p.id))
    db.add(o)
    db.commit()


def test_meses_e_despesas_que_valem():
    e = Expense(name="Aluguel", category="aluguel", amount_cents=200000, recurring=True,
                start_month=date(2026, 3, 1), end_month=date(2026, 6, 1))
    assert not e.applies_to(date(2026, 2, 1)) and e.applies_to(date(2026, 3, 1)) and e.applies_to(date(2026, 6, 1))
    assert not e.applies_to(date(2026, 7, 1))
    once = Expense(name="Conserto", amount_cents=1, recurring=False, start_month=date(2026, 5, 1))
    assert once.applies_to(date(2026, 5, 1)) and not once.applies_to(date(2026, 6, 1))
    assert add_months(date(2026, 11, 1), 2) == date(2027, 1, 1) and parse_month("2026-02") == date(2026, 2, 1)
    assert parse_month("x") is None


def test_dre_usa_a_conta_do_lucro_real(db):
    set_setting(db, "tax_rate_pct", 10.0)
    month = month_start(local_today())
    noon = datetime.combine(month, datetime.min.time()) + timedelta(hours=15)
    _sale(db, noon)                                         # receita 100, taxa 12, frete 15, custo 40
    _sale(db, noon + timedelta(hours=1), net=7000)          # ML com valor recebido: retido 30 = 12 + 18
    _sale(db, noon + timedelta(hours=2), cancelled=True)    # cancelada: fora
    _sale(db, datetime.combine(add_months(month, -1), datetime.min.time()) + timedelta(days=2, hours=15))
    db.add_all([Expense(name="Aluguel", category="aluguel", amount_cents=3000, recurring=True,
                        start_month=add_months(month, -1)),
                Expense(name="Contador", category="contador", amount_cents=500, recurring=False, start_month=month)])
    db.commit()
    d = month_dre(db, month)
    assert (d.revenue, d.fees, d.shipping, d.taxes, d.cost, d.orders) == (20000, 2400, 3300, 2000, 8000, 2)
    assert d.gross_profit == 20000 - 2400 - 3300 - 2000 - 8000 == 4300
    assert d.expenses == {"aluguel": 3000, "contador": 500} and d.net_profit == 800
    assert d.break_even == round(3500 / (4300 / 20000))
    previous = month_dre(db, add_months(month, -1))
    assert previous.revenue == 10000 and previous.expenses == {"aluguel": 3000}


def test_lancar_editar_excluir_despesa(logged_client, db):
    html = logged_client.get("/financeiro/despesas").text
    token = csrf_from(html)
    this = f"{month_start(local_today()):%Y-%m}"
    r = logged_client.post("/financeiro/despesas", data={"csrf_token": token, "name": "Aluguel do galpão",
                                                         "category": "aluguel", "amount": "2.500,00",
                                                         "recurring": "1", "start": this, "end": ""})
    assert "Aluguel do galpão" in r.text and "lançada" in r.text and "2.500,00 por mês" in r.text
    e = db.scalar(select(Expense))
    assert e.amount_cents == 250000 and e.recurring and e.end_month is None
    r = logged_client.post(f"/financeiro/despesas/{e.id}", data={"csrf_token": token, "name": "Aluguel do galpão",
                                                                "category": "aluguel", "amount": "2.700,00",
                                                                "recurring": "1", "start": this, "end": this})
    assert "Despesa atualizada" in r.text
    log = db.scalars(select(AuditLog).where(AuditLog.event == "financeiro.despesa_alterada")).one()
    assert log.details["antes"]["valor"] == "R$ 2.500,00" and log.details["depois"]["valor"] == "R$ 2.700,00"
    assert "Lucro líquido do mês" in logged_client.get("/financeiro/dre").text
    assert "Aluguel e condomínio" in logged_client.get("/financeiro/dre").text
    bad = logged_client.post("/financeiro/despesas", data={"csrf_token": token, "name": "", "amount": "abc",
                                                           "start": this})
    assert "Dê um nome" in bad.text and "Informe o valor" in bad.text
    logged_client.post(f"/financeiro/despesas/{e.id}/excluir", data={"csrf_token": token})
    assert db.scalar(select(Expense)) is None
    assert db.scalars(select(AuditLog).where(AuditLog.event == "financeiro.despesa_excluida")).one()
    assert "Excluiu uma despesa" in logged_client.get("/historico?tipo=financeiro").text


def test_permissoes_do_financeiro(client, db, user):
    _add(db, "fin@loja.com", "financeiro")
    login(client, "fin@loja.com")
    assert client.get("/financeiro/dre").status_code == 200 and "Lançar despesa" in client.get("/financeiro/despesas").text
    client.post("/logout", data={"csrf_token": csrf_from(client.get("/").text)})
    _add(db, "op@loja.com", "operador")
    login(client, "op@loja.com")
    assert client.get("/financeiro/dre").status_code == 403
    token = csrf_from(client.get("/").text)
    assert client.post("/financeiro/despesas", data={"csrf_token": token, "name": "x"}).status_code == 403
