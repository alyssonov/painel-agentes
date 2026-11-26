"""Busca por palavras em qualquer ordem (sem acento, sem maiúsculas), em todo o painel."""

from datetime import timedelta

from app.core.db import utcnow
from app.models import Listing, Order, OrderItem, Product
from app.models.enums import Channel, KanbanStatus
from app.models.product_extra import ProductExtra
from app.services.orders import OrderFilter, list_orders
from app.services.products import list_products
from app.services.search import matches, normalize, terms


def test_palavras_em_qualquer_ordem():
    title = "Protetor Escapamento Biz"
    assert matches(terms("protetor biz"), title)
    assert matches(terms("BIZ protetor"), title)
    assert matches(terms("escapamento"), title)
    assert not matches(terms("protetor cg"), title)  # todas as palavras precisam aparecer
    assert matches(terms("pecas"), "Moto Peças") and matches(terms("PEÇAS"), "moto pecas")
    assert matches(terms("cg160"), "Pastilha CG 160") and matches(terms("cg 160"), "Pastilha CG160")
    assert matches(terms("protetor de biz"), title)  # "de" é ignorado
    assert matches(terms("protetor"), "Biz", "SKU-PROTETOR")  # pode estar em campos diferentes
    assert normalize("  Retentor  Ø-30mm ") == "retentor 30mm" and terms("") == [] and matches([], "x")


def test_produtos(db):
    p = Product(sku="PROT-BIZ", name="Protetor Escapamento Biz", cost_cents=100)
    q = Product(sku="PAST-01", name="Pastilha de freio dianteira", cost_cents=100)
    db.add_all([p, q])
    db.flush()
    db.add(ProductExtra(product_id=q.id, compatibility=[{"marca": "Honda", "modelo": "Biz 125", "de": 2011, "ate": 2020}]))
    db.commit()
    assert [r.product.sku for r in list_products(db, q="protetor biz")] == ["PROT-BIZ"]
    assert {r.product.sku for r in list_products(db, q="biz")} == {"PROT-BIZ", "PAST-01"}  # Biz pela compatibilidade
    assert [r.product.sku for r in list_products(db, q="freio biz 125")] == ["PAST-01"]


def test_pedidos(db):
    o = Order(channel=Channel.ML, external_id="2000123", customer_short_name="Maria S.", items_total_cents=1,
              kanban_status=KanbanStatus.TO_PICK, ordered_at=utcnow() - timedelta(hours=1))
    o.items.append(OrderItem(sku="PROT-BIZ", title="Protetor Escapamento Biz", quantity=1, unit_price_cents=1))
    db.add(o)
    db.commit()
    assert [r.number for r in list_orders(db, OrderFilter(period="7", q="biz protetor")).rows] == ["2000123"]
    assert list_orders(db, OrderFilter(period="7", q="protetor cg")).rows == []


def test_tela_anuncios_busca_todos(logged_client, db):
    for i, title in enumerate(["Protetor Escapamento Biz 125", "Protetor de Carter CG", "Pastilha Biz"]):
        db.add(Listing(channel=Channel.ML, external_id=f"MLB{i}", item_id=f"MLB{i}", title=title, price_cents=4990,
                       status="active", synced_at=utcnow()))
    db.commit()
    html = logged_client.get("/anuncios?busca=protetor+biz").text
    assert "Protetor Escapamento Biz 125" in html and "Protetor de Carter CG" not in html and "Pastilha Biz" not in html
    assert "Nenhum anúncio tem todas essas palavras" in logged_client.get("/anuncios?busca=xyz+abc").text
    assert 'name="busca"' in logged_client.get("/anuncios").text
