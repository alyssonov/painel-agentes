"""Fase 4: Vigia de estoque, Conferência de NF-e, Margem real e as páginas."""

from datetime import timedelta

import respx
from sqlalchemy import select

from app.agents.margin import run_margin_agent
from app.agents.nfe_check import run_nfe_check
from app.agents.registry import ensure_agents
from app.agents.stock_watch import build_alerts as stock_alerts
from app.agents.stock_watch import run_stock_watch
from app.core.alerts import AlertSpec, sync_alerts
from app.core.db import get_sessionmaker, utcnow
from app.integrations.mercadolivre.mapping import listing_rows
from app.models import Agent, Alert, Invoice, Listing, Order, OrderItem, Product, StockSnapshot
from app.models.enums import AlertStatus, Channel, KanbanStatus
from app.services.margin_report import margin_report
from app.services.stock import stock_rows
from app.services.store_settings import set_setting
from tests.conftest import csrf_from
from tests.test_ml import ml_env  # noqa: F401 - fixture
from tests.olist_helpers import connect_account


def product(db, sku, cost=2000, price=5000, stock=None, reserved=0):
    p = Product(sku=sku, name=f"Produto {sku}", olist_id=f"o-{sku}", cost_cents=cost, price_cents=price)
    db.add(p)
    db.flush()
    if stock is not None:
        db.add(StockSnapshot(product_id=p.id, quantity=stock, reserved=reserved, captured_at=utcnow()))
    return p


def sale(db, prod, qty=1, days_ago=1, channel=Channel.ML, price=None, fee=0, shipping=0, status=KanbanStatus.SHIPPED,
         olist_status="Enviado", fees_known=True, invoice=None, hours_ago=None):
    price = price if price is not None else prod.price_cents
    when = utcnow() - (timedelta(hours=hours_ago) if hours_ago is not None else timedelta(days=days_ago))
    order = Order(channel=channel, external_id=f"E{prod.sku}{days_ago}{qty}{hours_ago}", olist_id=None,
                  olist_number=f"N{prod.id}{days_ago}{hours_ago}", customer_short_name="A",
                  ordered_at=when, kanban_status=status, olist_status=olist_status, items_total_cents=price * qty,
                  marketplace_fee_cents=fee, shipping_cost_cents=shipping, fees_known=fees_known,
                  invoice_olist_id=invoice)
    order.items.append(OrderItem(product_id=prod.id, sku=prod.sku, title=prod.name, quantity=qty,
                                 unit_price_cents=price))
    db.add(order)
    db.flush()
    return order


def enable(db, *slugs):
    ensure_agents(db)
    for agent in db.scalars(select(Agent).where(Agent.slug.in_(slugs))):
        agent.enabled = True
    db.commit()


# --- Alertas sem duplicar ------------------------------------------------------------

def test_alertas_criam_atualizam_e_resolvem(db):
    sync_alerts(db, "estoque", [AlertSpec("a", "atencao", "A"), AlertSpec("b", "critico", "B")])
    db.commit()
    sync_alerts(db, "estoque", [AlertSpec("a", "critico", "A mudou")])
    db.commit()
    alerts = {a.target_id: a for a in db.scalars(select(Alert))}
    assert len(alerts) == 2
    assert alerts["a"].status == AlertStatus.OPEN and alerts["a"].title == "A mudou"
    assert alerts["b"].status == AlertStatus.RESOLVED


# --- Estoque -----------------------------------------------------------------------------

def test_previsao_de_fim_do_estoque(db):
    acabando = product(db, "A", stock=6)
    sale(db, acabando, qty=30, days_ago=5)  # 30 em 30 dias = 1/dia → dura 6 dias
    esgotado = product(db, "B", stock=2, reserved=2)
    sale(db, esgotado, qty=3)
    product(db, "C", stock=40)  # sem vendas
    rows = {r.sku: r for r in stock_rows(db, window_days=30, alert_days=7)}
    assert rows["A"].status == "baixo" and round(rows["A"].days_left) == 6
    assert rows["B"].status == "esgotado" and rows["B"].available == 0
    assert rows["C"].status == "parado"


def test_divergencia_com_o_anuncio_ignora_full(db):
    p = product(db, "CAN-01", stock=5)
    db.add(Listing(channel=Channel.ML, external_id="MLB1", item_id="MLB1", sku="CAN-01", product_id=p.id,
                   title="Caneca", available_quantity=7, status="active", synced_at=utcnow()))
    db.add(Listing(channel=Channel.ML, external_id="MLB9", item_id="MLB9", sku="CAN-01", product_id=p.id,
                   title="Caneca Full", available_quantity=99, status="active", fulfillment=True, synced_at=utcnow()))
    db.flush()
    row = stock_rows(db)[0]
    assert row.diverges
    assert [l.diverges for l in row.listings] == [True, False]
    assert stock_rows(db, tolerance=2)[0].diverges is False


def test_alertas_de_estoque_tem_limite_e_resumo(db):
    for i in range(12):
        p = product(db, f"P{i}", stock=1)
        sale(db, p, qty=30)
    specs, low, _ = stock_alerts(stock_rows(db))
    assert low == 12 and len(specs) == 9 and specs[-1].key == "resumo"


def test_anuncio_com_variacoes_vira_uma_linha_por_variacao():
    from tests.ml_fake import FULL_ITEMS
    rows = listing_rows(FULL_ITEMS["MLB2"])
    assert [(r["external_id"], r["sku"], r["available_quantity"]) for r in rows] == [
        ("MLB2-11", "GAR-01", 30), ("MLB2-12", "GAR-02", 20)]
    assert all(r["fulfillment"] for r in rows)


@respx.mock
def test_sincronizacao_le_os_anuncios_do_ml(db, ml_env):
    from tests import ml_fake
    from tests.test_ml import connect_ml, sync
    product(db, "CAN-01", stock=5)
    db.commit()
    connect_ml(db)
    ml_fake.install(respx.mock)
    run = sync()
    assert run.counts["anuncios"] == 3
    caneca = db.scalar(select(Listing).where(Listing.external_id == "MLB1"))
    assert caneca.product_id is not None and caneca.available_quantity == 7


def test_agente_de_estoque_gera_alertas(db):
    connect_account(db)
    enable(db, "estoque")
    p = product(db, "A", stock=2)
    sale(db, p, qty=30)
    db.commit()
    run = run_stock_watch(get_sessionmaker())
    assert run.status == "ok" and "estoque baixo" in run.summary
    alert = db.scalar(select(Alert).where(Alert.source == "estoque"))
    assert "acaba em cerca de 2 dias" in alert.title


def test_agentes_nao_rodam_sem_conta_real(db):
    enable(db, "estoque", "nfe", "margem")
    sm = get_sessionmaker()
    assert run_stock_watch(sm) is None and run_nfe_check(sm) is None and run_margin_agent(sm) is None


# --- NF-e ---------------------------------------------------------------------------------

def test_conferencia_de_nfe(db):
    connect_account(db)
    enable(db, "nfe")
    p = product(db, "A")
    sem_nota = sale(db, p, status=KanbanStatus.PACKED, olist_status="Pronto para envio", hours_ago=2)
    com_nota = sale(db, p, qty=2, status=KanbanStatus.PACKED, olist_status="Pronto para envio", hours_ago=3)
    db.add(Invoice(olist_id="n1", order_id=com_nota.id, status_code=6, status_label="Autorizada"))
    parado = sale(db, p, qty=3, status=KanbanStatus.TO_PICK, olist_status="Aprovado", hours_ago=30)
    recente = sale(db, p, qty=4, status=KanbanStatus.TO_PICK, olist_status="Aprovado", hours_ago=2)
    db.add(Invoice(olist_id="n2", number="124", order_id=None, status_code=5, status_label="Rejeitada",
                   issued_at=utcnow()))
    db.commit()
    run = run_nfe_check(get_sessionmaker())
    assert run.summary == "2 pedido(s) sem nota · 1 nota(s) rejeitada(s)"
    keys = {a.target_id: a for a in db.scalars(select(Alert).where(Alert.source == "nfe"))}
    assert keys[f"pedido:{sem_nota.id}"].severity == "critico"
    assert keys[f"pedido:{parado.id}"].severity == "atencao" and "30 h" in keys[f"pedido:{parado.id}"].title
    assert f"pedido:{com_nota.id}" not in keys and f"pedido:{recente.id}" not in keys
    assert any("rejeitada" in a.title for a in keys.values())

    # A nota foi emitida no Olist: o alerta some sozinho na próxima rodada.
    db.add(Invoice(olist_id="n3", order_id=sem_nota.id, status_code=6, status_label="Autorizada"))
    db.commit()
    run_nfe_check(get_sessionmaker())
    db.expire_all()
    assert db.scalar(select(Alert).where(Alert.target_id == f"pedido:{sem_nota.id}")).status == AlertStatus.RESOLVED


def test_agente_de_nfe_nunca_escreve():
    """Regra do projeto: a conferência de NF-e só lê (nenhum import de escrita ou do gateway)."""
    source = open("app/agents/nfe_check.py", encoding="utf-8").read()
    assert "writes" not in source and "actions" not in source and ".post(" not in source


# --- Margem -----------------------------------------------------------------------------------

def test_relatorio_de_margem_por_produto(db):
    set_setting(db, "tax_rate_pct", 10.0)
    bom = product(db, "BOM", cost=2000, price=10000)
    ruim = product(db, "RUIM", cost=4000, price=4500)
    sem_custo = product(db, "SC", cost=0, price=3000)
    sale(db, bom, qty=2, fee=2800, shipping=0)
    sale(db, ruim, qty=1, fee=900, shipping=0)
    sale(db, sem_custo, qty=1, fees_known=False)
    now = utcnow()
    report = margin_report(db, now - timedelta(days=7), now, 10.0)
    by = {p.sku: p for p in report.products}
    # BOM: 200 − 28 − 20 (imposto) − 40 = 112
    assert by["BOM"].profit == 11200
    # RUIM: 45 − 9 − 4,50 − 40 = −8,50
    assert by["RUIM"].profit == -850 and report.products[0].sku == "RUIM"
    assert by["SC"].missing_cost and report.products_missing_cost == 1
    assert report.orders_missing_fees == 1
    assert len(report.loss_orders) == 1


def test_agente_de_margem_avisa_prejuizo_e_sem_custo(db):
    connect_account(db)
    enable(db, "margem")
    set_setting(db, "tax_rate_pct", 10.0)
    ruim = product(db, "RUIM", cost=4000, price=4500)
    sale(db, ruim, fee=900)
    sale(db, product(db, "SC", cost=0, price=3000))
    db.commit()
    run = run_margin_agent(get_sessionmaker())
    assert "1 produto(s) no prejuízo" in run.summary
    titles = [a.title for a in db.scalars(select(Alert).where(Alert.source == "margem"))]
    assert any("deu prejuízo" in t for t in titles) and any("sem custo" in t for t in titles)


# --- Páginas ----------------------------------------------------------------------------------

def test_paginas_de_estoque_e_margem(logged_client):
    html = logged_client.get("/estoque-precos?filtro=todos").text
    assert "Disponível no Olist" in html and "Esgotados" in html
    html = logged_client.get("/margem?periodo=30").text
    assert "Lucro real" in html and "Por produto" in html and "Vendas com prejuízo" in html
    assert logged_client.get("/margem?periodo=xyz").status_code == 200


def test_regras_dos_agentes(logged_client, db):
    html = logged_client.get("/agentes").text
    assert "Avisar quando o estoque durar menos de" in html
    token = csrf_from(html)
    r = logged_client.post("/agentes/estoque/config", data={"csrf_token": token, "dias_alerta": "10",
                                                            "janela_vendas_dias": "14", "tolerancia_divergencia": "1"})
    assert "regras salvas" in r.text
    db.expire_all()
    assert db.scalar(select(Agent).where(Agent.slug == "estoque")).config["dias_alerta"] == 10
    r = logged_client.post("/agentes/estoque/config", data={"csrf_token": token, "dias_alerta": "999",
                                                            "janela_vendas_dias": "14", "tolerancia_divergencia": "1"})
    assert "fora do permitido" in r.text


@respx.mock
def test_consulta_em_lote_recusada_usa_um_por_vez(db, ml_env):
    """O Mercado Livre recusou /items?ids= (403) para o usuário: o painel lê um anúncio por vez."""
    import httpx

    from tests import ml_fake
    from tests.test_ml import connect_ml, sync
    product(db, "CAN-01", stock=5)
    db.commit()
    connect_ml(db)
    routes = ml_fake.install(respx.mock)
    routes["items"].mock(return_value=httpx.Response(403, json={"message": "forbidden"}))
    for item_id, body in ml_fake.FULL_ITEMS.items():
        respx.get(f"{ml_fake.BASE_URL}/items/{item_id}").mock(return_value=httpx.Response(200, json=body))
    run = sync()
    assert run.status == "ok", run.error
    assert run.counts["anuncios"] == 3
    assert db.scalar(select(Listing).where(Listing.external_id == "MLB1")).available_quantity == 7


@respx.mock
def test_falha_nos_anuncios_nao_para_perguntas_e_taxas(db, ml_env):
    import httpx

    from tests import ml_fake
    from tests.test_ml import connect_ml, sync
    connect_ml(db)
    routes = ml_fake.install(respx.mock)
    routes["my_items"].mock(return_value=httpx.Response(403, json={"message": "forbidden"}))
    run = sync()
    assert run.status == "ok", run.error
    assert run.counts["perguntas"] == 3 and run.counts["anuncios_erro"] == 1
