"""Fornecedores e pedidos de compra; sugestão de compra descontando o que está a caminho."""

from datetime import date

from sqlalchemy import select

from app.core.timeutil import local_today
from app.models import AuditLog
from app.models.product_extra import ProductExtra
from app.models.purchase import PurchaseOrder, PurchaseOrderItem, Supplier
from app.services.purchase import purchase_suggestions
from app.services.stock import stock_rows
from tests.conftest import csrf_from, login
from tests.olist_helpers import connect_account
from tests.test_permissions import _add
from tests.test_phase4 import product, sale


def _open_order(db, sku, qty, supplier=None):
    supplier = supplier or Supplier(name="Dist X", lead_time_days=5)
    o = PurchaseOrder(number=1, supplier=supplier, status="aberto", ordered_on=date(2026, 11, 20),
                      items=[PurchaseOrderItem(sku=sku, name=sku, quantity=qty)])
    db.add(o)
    db.commit()
    return o


def test_sugestao_desconta_o_que_esta_a_caminho(db):
    fast = product(db, "RAPIDO", cost=1500, stock=5)
    sale(db, fast, qty=30, days_ago=3)  # 1 por dia
    db.commit()
    rows = stock_rows(db, window_days=30)
    (before,) = purchase_suggestions(db, rows, lead_days=10, coverage_days=30)
    assert before.quantity == 35 and before.on_order == 0
    order = _open_order(db, "RAPIDO", 8)
    (after,) = purchase_suggestions(db, rows, lead_days=10, coverage_days=30)
    # tem 5 + 8 a caminho = 13 dias (≤ prazo 10 + 7: esta semana); precisa 40 → comprar 27
    assert after.on_order == 8 and after.quantity == 27 and after.urgency == "semana"
    _open_order_more = PurchaseOrder(number=2, supplier=order.supplier, status="aberto", ordered_on=date(2026, 11, 21),
                                     items=[PurchaseOrderItem(sku="RAPIDO", name="x", quantity=30)])
    db.add(_open_order_more)
    db.commit()
    assert purchase_suggestions(db, rows, lead_days=10, coverage_days=30) == []  # 43 dias: nada a comprar
    _open_order_more.status = "cancelado"
    order.status = "recebido"
    db.commit()
    (again,) = purchase_suggestions(db, rows, lead_days=10, coverage_days=30)
    assert again.on_order == 0 and again.quantity == 35


def test_prazo_e_minimo_do_produto(db):
    slow = product(db, "MINIMO", cost=100, stock=2)  # sem vendas, mas abaixo do mínimo
    db.add(ProductExtra(product_id=slow.id, min_stock=10, compatibility=[]))
    other = product(db, "PRAZO", cost=100, stock=20)
    sale(db, other, qty=30, days_ago=3)  # 1/dia: dura 20 dias
    db.add(ProductExtra(product_id=other.id, lead_time_days=25, supplier="Dist X", compatibility=[]))
    db.commit()
    items = {i.sku: i for i in purchase_suggestions(db, stock_rows(db, window_days=30), lead_days=10,
                                                    coverage_days=30)}
    assert items["MINIMO"].quantity == 8 and items["MINIMO"].urgency == "agora" and items["MINIMO"].days_left is None
    assert items["PRAZO"].lead_days == 25 and items["PRAZO"].urgency == "agora" and items["PRAZO"].quantity == 35


def test_fluxo_completo_na_tela(logged_client, db):
    connect_account(db)
    prod = product(db, "CAN-01", cost=2500, stock=3)
    sale(db, prod, qty=30, days_ago=1)
    db.commit()
    token = csrf_from(logged_client.get("/compras/fornecedores").text)
    r = logged_client.post("/compras/fornecedores", data={"csrf_token": token, "name": "Moto Peças SP",
                                                          "lead_time_days": "7", "phone": "11 99999-0000"})
    assert "Fornecedor Moto Peças SP salvo" in r.text
    supplier = db.scalar(select(Supplier))
    assert "CAN-01; 37" in logged_client.get("/compras/novo?sugestao=1").text  # sugestão preenchida
    r = logged_client.post("/compras/novo", data={"csrf_token": token, "supplier_id": str(supplier.id),
                                                  "lines": "CAN-01; 30; 24,00\nNAO-EXISTE; 2"})
    assert "não está no Olist" in r.text and db.scalar(select(PurchaseOrder)) is None
    r = logged_client.post("/compras/novo", data={"csrf_token": token, "supplier_id": str(supplier.id),
                                                  "lines": "can-01; 10; 24,00"})
    assert "Pedido de compra nº 1 registrado (10 unidade(s))" in r.text
    order = db.scalar(select(PurchaseOrder))
    assert order.expected_on is not None and (order.expected_on - local_today()).days == 7
    assert order.items[0].unit_cost_cents == 2400 and order.total_cents == 24000
    stock = logged_client.get("/estoque-precos").text
    assert "A caminho" in stock and ">10<" in stock and "27 un." in stock  # 37 − 10
    csv = logged_client.get(f"/compras/{order.id}.csv")
    assert csv.status_code == 200 and "CAN-01" in csv.text and "R$ 240,00" in csv.text
    assert "pedido-compra-1-moto-pecas-sp.csv" in csv.headers["content-disposition"]
    r = logged_client.post(f"/compras/{order.id}/situacao", data={"csrf_token": token, "status": "recebido"})
    assert "dar a entrada da mercadoria no Olist" in r.text
    db.refresh(order)
    assert order.status == "recebido" and order.received_on == local_today()
    events = {a.event for a in db.scalars(select(AuditLog))}
    assert {"compras.fornecedor_salvo", "compras.pedido_criado", "compras.pedido_situacao"} <= events


def test_operacao_registra_sem_ver_custo(client, db, user):
    prod = product(db, "RET-01", cost=500, stock=1)
    db.add(Supplier(name="Dist Y"))
    db.commit()
    _add(db, "op@loja.com", "operador")
    login(client, "op@loja.com")
    token = csrf_from(client.get("/compras/novo").text)
    supplier = db.scalar(select(Supplier))
    client.post("/compras/novo", data={"csrf_token": token, "supplier_id": str(supplier.id),
                                       "lines": "RET-01; 10; 99,00"})
    order = db.scalar(select(PurchaseOrder))
    assert order.items[0].unit_cost_cents is None
    html = client.get(f"/compras/{order.id}").text
    assert "Custo un." not in html and prod.name in html
    client.post("/logout", data={"csrf_token": token})
    _add(db, "at@loja.com", "atendimento")
    login(client, "at@loja.com")
    token = csrf_from(client.get("/compras").text)
    assert client.post("/compras/fornecedores", data={"csrf_token": token, "name": "Z"}).status_code == 403
