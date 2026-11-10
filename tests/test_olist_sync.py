"""Sincronização Olist → banco local."""

from datetime import timedelta

import httpx
import respx
from sqlalchemy import select

from app.core.db import get_sessionmaker, utcnow
from app.integrations.olist.client import BASE_URL
from app.models import Alert, IntegrationAccount, Invoice, Order, Product, StockSnapshot, SyncRun
from app.models.enums import AlertStatus, Channel, IntegrationStatus, KanbanStatus, SyncStatus
from app.sync.olist_sync import run_olist_sync
from tests import olist_fake
from tests.olist_helpers import connect_account, no_sleep_client_factory


def sync():
    return run_olist_sync(get_sessionmaker(), no_sleep_client_factory())


def test_sem_conta_conectada_nao_faz_nada(db):
    assert sync() is None


@respx.mock
def test_primeira_sincronizacao_importa_tudo(db, olist_env):
    connect_account(db)
    routes = olist_fake.install(respx.mock)
    run = sync()
    assert run.status == SyncStatus.OK, run.error
    assert run.counts == {"produtos": 2, "estoque": 2, "pedidos": 4, "notas": 2}

    # Produtos: custo médio quando existe, senão preço de custo; excluídos ficam de fora.
    caneca = db.scalar(select(Product).where(Product.sku == "CAN-01"))
    garrafa = db.scalar(select(Product).where(Product.sku == "GAR-01"))
    assert (caneca.cost_cents, caneca.price_cents) == (2150, 6990)
    assert garrafa.cost_cents == 3450
    assert db.scalar(select(Product).where(Product.sku == "OLD-01")) is None

    # Estoque: só de produtos conhecidos.
    snaps = {s.product_id: s for s in db.scalars(select(StockSnapshot))}
    assert snaps[caneca.id].quantity == 4 and snaps[caneca.id].reserved == 1

    # Pedidos.
    orders = {o.olist_id: o for o in db.scalars(select(Order))}
    assert orders["1"].channel == Channel.ML and orders["1"].kanban_status == KanbanStatus.TO_PICK
    assert orders["2"].channel == Channel.SHOPEE and orders["2"].kanban_status == KanbanStatus.SHIPPED
    assert orders["3"].kanban_status == KanbanStatus.CANCELLED
    assert orders["4"].channel == Channel.OTHER and orders["4"].problem_note == "Dados incompletos"
    o1 = orders["1"]
    assert o1.customer_short_name == "Maria S."  # sem nome completo nem CPF
    assert o1.items_total_cents == 13980 and o1.shipping_cost_cents == 2290
    assert o1.fees_known is False  # a taxa do marketplace chega na Fase 3
    assert len(o1.items) == 1 and o1.items[0].product_id == caneca.id  # serviço não entra
    assert o1.external_id == "EC1" and o1.olist_number == "5001"
    assert o1.ordered_at.hour == 13  # 10:15 em Brasília = 13:15 UTC

    # Notas ligadas aos pedidos.
    notas = {i.olist_id: i for i in db.scalars(select(Invoice))}
    assert notas["7001"].order_id == o1.id and notas["7001"].status_label == "Autorizada"
    assert notas["7002"].status_label == "Rejeitada"

    # Primeira vez: pedidos pela data de criação; conta marcada como OK.
    assert "dataInicial" in routes["pedidos"].calls[0].request.url.params
    account = db.scalar(select(IntegrationAccount))
    db.refresh(account)
    assert account.status == IntegrationStatus.OK and account.last_ok_at is not None
    assert "cursor_pedidos" in account.extra


@respx.mock
def test_proximas_vezes_busca_so_o_que_mudou_e_nao_duplica(db, olist_env):
    connect_account(db)
    routes = olist_fake.install(respx.mock)
    sync()
    sync()
    regular = [c.request.url.params for c in routes["pedidos"].calls if "dataFinal" not in c.request.url.params]
    last = regular[-1]
    assert "dataAtualizacao" in last and "dataInicial" not in last
    assert "dataAlteracao" in routes["produtos"].calls[-1].request.url.params
    assert db.query(Order).count() == 4
    assert db.query(Product).count() == 2


@respx.mock
def test_falha_vira_alerta_e_volta_ao_normal_quando_recupera(db, olist_env):
    connect_account(db)
    respx.get(f"{BASE_URL}/produtos").mock(return_value=httpx.Response(503))
    run = sync()
    assert run.status == SyncStatus.ERROR
    alert = db.scalar(select(Alert).where(Alert.source == "olist"))
    assert alert.status == AlertStatus.OPEN and alert.severity == "critico"
    assert db.scalar(select(IntegrationAccount)).status == IntegrationStatus.ERROR

    sync()  # falha de novo: não duplica o alerta
    assert db.query(Alert).filter(Alert.source == "olist").count() == 1

    respx.mock.reset()
    respx.mock.routes.clear()
    olist_fake.install(respx.mock)
    assert sync().status == SyncStatus.OK
    db.expire_all()
    assert db.scalar(select(Alert).where(Alert.source == "olist")).status == AlertStatus.RESOLVED


def test_nao_roda_duas_ao_mesmo_tempo(db, olist_env):
    connect_account(db)
    db.add(SyncRun(provider="olist", status=SyncStatus.RUNNING, started_at=utcnow()))
    db.commit()
    assert sync() is None


@respx.mock
def test_execucao_travada_antiga_nao_bloqueia(db, olist_env):
    connect_account(db)
    db.add(SyncRun(provider="olist", status=SyncStatus.RUNNING, started_at=utcnow() - timedelta(hours=2)))
    db.commit()
    olist_fake.install(respx.mock)
    assert sync().status == SyncStatus.OK


@respx.mock
def test_historico_antigo_e_importado_aos_poucos(db, olist_env, monkeypatch):
    """Depois da 1ª leitura, uma semana de vendas antigas por rodada, sem repetir pedido."""
    from datetime import datetime

    from app.sync import olist_sync
    from app.sync.common import get_cursor
    connect_account(db)
    routes = olist_fake.install(respx.mock)
    sync()  # 1ª leitura: sem histórico ainda
    assert not [c for c in routes["pedidos"].calls if "dataFinal" in c.request.url.params]
    details_before = routes["pedido"].call_count
    sync()
    history = [c.request.url.params for c in routes["pedidos"].calls if "dataFinal" in c.request.url.params]
    assert len(history) == 1
    start = datetime.fromisoformat(history[0]["dataInicial"])
    end = datetime.fromisoformat(history[0]["dataFinal"])
    assert (end - start).days == olist_sync.HISTORY_CHUNK_DAYS
    # Os pedidos da listagem falsa já estavam no banco: nenhum detalhe novo foi consultado por causa do histórico.
    assert routes["pedido"].call_count - details_before <= len(olist_fake.ORDERS)
    account = db.scalar(select(IntegrationAccount))
    db.refresh(account)
    assert get_cursor(account, "historico_ate").date() == start.date()
    assert db.query(Order).count() == 4
    # Quando chega ao limite configurado, para de buscar.
    monkeypatch.setenv("OLIST_HISTORY_DAYS", "1")
    from app.config import get_settings
    get_settings.cache_clear()
    calls = routes["pedidos"].call_count
    sync()
    get_settings.cache_clear()
    assert not [c for c in routes["pedidos"].calls[calls:] if "dataFinal" in c.request.url.params]
