"""Central de pedidos: situação do envio, filtros, planilha e lista de separação."""

from datetime import timedelta

from app.core.db import utcnow
from app.core.timeutil import local_day_bounds_utc, local_today
from app.models import Order, OrderItem, Product, Shipment, StockSnapshot
from app.models.enums import Channel, KanbanStatus
from app.services.orders import OrderFilter, list_orders, pick_list
from tests.conftest import login
from tests.test_permissions import _add


def _order(db, ref, *, channel=Channel.ML, hours_ago=2, status=KanbanStatus.TO_PICK, sku="PAST-01",
           title="Pastilha de freio CG 160", qty=1, total=4990, ship=None, invoice=None):
    o = Order(channel=channel, external_id=ref, customer_short_name="Maria S.", kanban_status=status,
              items_total_cents=total, ordered_at=utcnow() - timedelta(hours=hours_ago), invoice_olist_id=invoice)
    o.items.append(OrderItem(sku=sku, title=title, quantity=qty, unit_price_cents=total // qty))
    db.add(o)
    if ship:
        status_, deadline_h, substatus, logistic = ship
        db.add(Shipment(channel=Channel.ML, shipment_id=f"S{ref}", order_ref=ref, status=status_, substatus=substatus,
                        logistic_type=logistic, checked_at=utcnow(),
                        dispatch_deadline=utcnow() + timedelta(hours=deadline_h) if deadline_h is not None else None))
    db.commit()
    return o


def _hours_left_today() -> float:
    """Horas até o fim de HOJE no fuso da loja (o teste não pode depender da hora em que roda)."""
    end = local_day_bounds_utc(local_today())[1]
    return max((end - utcnow()).total_seconds() / 3600 - 0.01, 0.001)


def _setup(db):
    _order(db, "A1", ship=("ready_to_ship", _hours_left_today(), None, None))   # a enviar hoje
    _order(db, "A2", ship=("ready_to_ship", -1, None, None), qty=2, total=9980)  # atrasado
    _order(db, "A3", ship=("shipped", -20, None, None), invoice="nf1")           # enviado
    _order(db, "A4", ship=("ready_to_ship", -2, "picked_up", None))             # coletado = saiu
    _order(db, "A5", ship=("ready_to_ship", 2, None, "fulfillment"))            # Full
    _order(db, "A6", ship=("ready_to_ship", 50, None, None), sku="RET-02", title="Retentor")  # prazo amanhã
    _order(db, "S1", channel=Channel.SHOPEE, sku="RET-02", title="Retentor")    # Shopee sem prazo: a separar
    _order(db, "C1", status=KanbanStatus.CANCELLED)
    _order(db, "OLD", hours_ago=24 * 20)


def test_situacao_de_cada_pedido(db):
    _setup(db)
    rows = {r.number: r.situation for r in list_orders(db, OrderFilter(period="7")).rows}
    assert rows == {"A1": "a_enviar", "A2": "atrasado", "A3": "enviado", "A4": "enviado", "A5": "full",
                    "A6": "a_enviar", "S1": "a_enviar", "C1": "cancelado"}  # OLD está fora dos 7 dias
    assert "OLD" in {r.number for r in list_orders(db, OrderFilter(period="30")).rows}


def test_filtros_e_totais(db):
    _setup(db)
    late = list_orders(db, OrderFilter(period="7", situation="atrasado"))
    assert [r.number for r in late.rows] == ["A2"] and late.revenue_cents == 9980
    assert [r.number for r in list_orders(db, OrderFilter(period="7", channel=Channel.SHOPEE)).rows] == ["S1"]
    assert {r.number for r in list_orders(db, OrderFilter(period="7", q="retentor")).rows} == {"A6", "S1"}
    assert [r.number for r in list_orders(db, OrderFilter(period="7", q="a3")).rows] == ["A3"]
    full = list_orders(db, OrderFilter(period="7"))
    assert full.counts["cancelado"] == 1 and full.revenue_cents == full.revenue_cents  # cancelado fora da soma
    assert sum(r.total_cents for r in full.rows if r.situation != "cancelado") == full.revenue_cents


def test_lista_de_separacao_soma_por_produto_e_avisa_falta(db):
    _setup(db)
    p = Product(sku="PAST-01", name="Pastilha", cost_cents=1000)
    db.add(p)
    db.flush()
    db.add(StockSnapshot(product_id=p.id, quantity=2, reserved=0, captured_at=utcnow()))
    db.commit()
    result = pick_list(db)
    lines = {li.sku: li for li in result.lines}
    # A1 (1) + A2 atrasado (2) = 3 pastilhas; A6 tem prazo amanhã (fica de fora); S1 Shopee sem prazo entra.
    assert lines["PAST-01"].quantity == 3 and sorted(lines["PAST-01"].orders) == ["A1", "A2"]
    assert lines["PAST-01"].short and lines["PAST-01"].stock == 2
    assert lines["RET-02"].orders == ["S1"]
    assert result.orders == 3 and result.late == 1 and result.units == 4


def test_paginas_e_planilha(logged_client, db):
    _setup(db)
    html = logged_client.get("/pedidos?periodo=7&situacao=atrasado").text
    assert "A2" in html and "Atrasado" in html and "A1</a>" not in html
    assert 'href="/margem/venda/' in html  # dono vê o link da conta da venda
    csv = logged_client.get("/pedidos.csv?periodo=7")
    assert csv.headers["content-type"].startswith("text/csv") and "A3" in csv.text and "Emitida" not in csv.text
    assert "sim" in csv.text  # nota fiscal emitida em A3
    pick = logged_client.get("/pedidos/separacao").text
    assert "Lista de separação" in pick and "data-imprimir" in pick and "style=" not in pick


def test_sem_financeiro_nao_ve_link_da_conta(client, db, user):
    _setup(db)
    _add(db, "op@loja.com", "operador")
    login(client, "op@loja.com")
    html = client.get("/pedidos").text
    assert "A1" in html and 'href="/margem/venda/' not in html
