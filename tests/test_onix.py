"""Onix (loja física): leitura de custo, marca, qtd. mínima — só leitura, sem rede real."""

import inspect

import httpx
import pytest
import respx
from sqlalchemy import select

from app.config import get_settings
from app.core.db import get_sessionmaker
from app.integrations.onix.client import OnixClient
from app.integrations.onix.mapping import parse, to_cents
from app.models import AuditLog, Product
from app.models.enums import SyncStatus
from app.models.product_extra import ProductExtra
from app.services.products import list_products
from app.sync.onix_sync import run_onix_sync
from tests.conftest import csrf_from

BASE = "http://onix.teste:8020"
URL = f"{BASE}/api-produto/get-produto-por-id"

# Como o Onix devolve (exemplo real do fornecedor, só os campos usados + alguns extras).
SEMI_KIT = {"cod_prod": 1550, "descr_prod": "SEMI KIT T.150 ", "marca_prod": "ALLEN", "custo_prod": "24.700000",
            "vlmedio_prod": "25.49", "custoatac_prod": "24.700000", "qtdminima_prod": "20.00",
            "codfab_prod": "81730", "local_prod": "", "ultcomp_prod": "2023-02-07", "sku": None,
            "cod_cfiscal": {"codfiscal_cfiscal": "8714.10.00"}}


@pytest.fixture
def onix_env(monkeypatch):
    monkeypatch.setenv("ONIX_API_URL", BASE)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _fake(router, products):
    def handler(request):
        data = products.get(request.url.params["id"])
        return httpx.Response(200, json=data) if data else httpx.Response(404)
    return router.get(URL).mock(side_effect=handler)


def test_leitura_dos_campos():
    p = parse(SEMI_KIT)
    assert (p.code, p.brand, p.cost_cents, p.min_qty, p.factory_code, p.location) == \
        ("1550", "ALLEN", 2470, 20, "81730", None)
    assert parse(SEMI_KIT, "vlmedio_prod").cost_cents == 2549 and parse(SEMI_KIT, "inventado").cost_cents == 2470
    assert to_cents("0.000000") is None and to_cents("null") is None and to_cents("abc") is None


def test_cliente_so_tem_leitura():
    public = [n for n, _ in inspect.getmembers(OnixClient, inspect.isfunction) if not n.startswith("_")]
    assert public == ["get_product"]


@respx.mock
def test_sincronizacao_preenche_sem_sobrescrever(db, onix_env):
    semi = Product(sku="1550", name="Semi kit", cost_cents=2200)
    manual = Product(sku="2000", name="Retentor", cost_cents=500, brand="Sabó")
    missing = Product(sku="9999", name="Só no Olist", cost_cents=100)
    db.add_all([semi, manual, missing])
    db.flush()
    db.add(ProductExtra(product_id=manual.id, min_stock=3, supplier_code="X-1", compatibility=[]))
    db.commit()
    other = dict(SEMI_KIT, cod_prod=2000, marca_prod="SABO", custo_prod="5.10", qtdminima_prod="50", codfab_prod="F2")
    route = _fake(respx, {"1550": SEMI_KIT, "2000": other})
    run = run_onix_sync(get_sessionmaker(), client=OnixClient(BASE, pause=0, retry_wait=0))
    assert run.status == SyncStatus.OK
    assert run.counts == {"consultados": 3, "atualizados": 2, "custos_mudaram": 2, "nao_encontrados": 1}
    assert all(c.request.method == "GET" for c in route.calls)
    extras = {e.product_id: e for e in db.scalars(select(ProductExtra))}
    e1, e2 = extras[semi.id], extras[manual.id]
    assert (e1.onix_cost_cents, e1.onix_brand, e1.min_stock, e1.supplier_code) == (2470, "ALLEN", 20, "81730")
    assert (e2.onix_cost_cents, e2.min_stock, e2.supplier_code) == (510, 3, "X-1")  # o que já existia ficou
    db.refresh(semi)
    assert semi.cost_cents == 2200  # custo do Olist (usado no lucro) não muda
    rows = {r.product.sku: r for r in list_products(db)}
    assert (rows["1550"].brand, rows["1550"].brand_source) == ("ALLEN", "Onix")
    assert rows["2000"].brand == "Sabó"  # marca do Olist vale mais que a do Onix
    log = db.scalars(select(AuditLog).where(AuditLog.event == "produto.cadastro_alterado")).first()
    assert log.details["origem"] == "Onix" and log.details["depois"]["custo_onix"] == "R$ 24,70"
    # 2ª leitura sem mudança: não grava histórico de novo
    run2 = run_onix_sync(get_sessionmaker(), client=OnixClient(BASE, pause=0, retry_wait=0))
    assert run2.counts["atualizados"] == 0


@respx.mock
def test_onix_desligado_para_logo(db, onix_env):
    for i in range(8):
        db.add(Product(sku=str(100 + i), name=f"P{i}", cost_cents=1))
    db.commit()
    route = respx.get(URL).mock(side_effect=httpx.ConnectError("sem rede"))
    run = run_onix_sync(get_sessionmaker(), client=OnixClient(BASE, pause=0, retry_wait=0))
    assert run.status == SyncStatus.ERROR and "não respondeu" in run.error
    assert route.call_count <= 5 * 3  # para depois de 5 produtos seguidos sem resposta (3 tentativas cada)


def test_sem_endereco_nao_faz_nada(db):
    assert run_onix_sync(get_sessionmaker()) is None


def test_tela_contas_conectadas(logged_client, db, onix_env, monkeypatch):
    html = logged_client.get("/integracoes").text
    assert "Onix" in html and "Atualizar custos" in html and "aberta na internet sem senha" in html
    called = []
    monkeypatch.setattr("app.web.routes.integrations._onix_sync_in_background", lambda: called.append(1))
    r = logged_client.post("/integracoes/onix/sincronizar", data={"csrf_token": csrf_from(html)})
    assert "Leitura do Onix iniciada" in r.text and called == [1]


def test_tela_sem_configurar(logged_client):
    assert "ONIX_API_URL" in logged_client.get("/integracoes").text
