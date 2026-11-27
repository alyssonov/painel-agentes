"""Mercado Ads: leitura das campanhas, dia a dia e anúncios; tela, DRE e alerta. API simulada."""

from datetime import timedelta

import httpx
import respx
from sqlalchemy import func, select

from app.core.timeutil import local_today
from app.models import AdCampaign, AdDaily, AdGroup, Alert, IntegrationAccount
from app.models.enums import AlertStatus
from app.services.ads import ads_summary
from app.services.dre import ADS_CATEGORY, month_dre
from tests import ml_fake
from tests.test_ml import connect_ml, ml_env, sync  # noqa: F401 - fixture

API = "https://api.mercadolibre.com"
ADV = f"{API}/advertising/MLB/advertisers/777/product_ads"


def _ads(router, *, campaigns=None, daily=None, groups=None):
    router.get(f"{API}/advertising/advertisers").mock(return_value=httpx.Response(200, json={"advertisers": [
        {"advertiser_id": 999, "site_id": "MLA"}, {"advertiser_id": 777, "site_id": "MLB"}]}))
    camp = router.get(f"{ADV}/campaigns/search").mock(return_value=httpx.Response(200, json={
        "paging": {"total": len(campaigns or []), "offset": 0, "limit": 50}, "results": campaigns or []}))
    day = router.get(url__regex=rf"{API}/advertising/MLB/product_ads/campaigns/\d+").mock(
        return_value=httpx.Response(200, json=daily or []))
    grp = router.get(f"{ADV}/ad_groups/search").mock(return_value=httpx.Response(200, json={
        "paging": {"total": len(groups or []), "offset": 0, "limit": 200}, "results": groups or []}))
    return camp, day, grp


TODAY = local_today()
CAMPAIGN = {"id": 355189450, "name": "Pastilhas", "status": "active", "strategy": "PROFITABILITY", "budget": 30.0,
            "roas_target": 6.0, "metrics": {"cost": 120.5, "total_amount": 903.75, "direct_amount": 800.0,
                                            "clicks": 210, "prints": 9000, "units_quantity": 12,
                                            "advertising_items_quantity": 11}}
DAILY = [{"date": (TODAY - timedelta(days=1)).isoformat(), "cost": 20.0, "total_amount": 150.0, "clicks": 30,
          "advertising_items_quantity": 2},
         {"date": TODAY.isoformat(), "cost": 10.0, "total_amount": 0.0, "clicks": 9}]
GROUPS = [{"id": 1194808653, "ad_group_external_id": "MLB123", "ad_group_type": "ITEM", "title": "Retentor CG",
           "campaign_id": 355189450, "status": "ACTIVE",
           "metrics": {"clicks": 40, "cost": 45.0, "total_amount": 0.0, "advertising_items_quantity": 0}},
          {"id": 1194727929, "ad_group_external_id": "MLB456", "ad_group_type": "ITEM", "title": "Pastilha CG",
           "campaign_id": 355189450, "status": "ACTIVE",
           "metrics": {"clicks": 80, "cost": 60.0, "total_amount": 700.0, "advertising_items_quantity": 9}}]


@respx.mock
def test_le_campanhas_dia_a_dia_e_anuncios(db, ml_env):  # noqa: F811
    ml_fake.install(respx)
    camp, day, grp = _ads(respx, campaigns=[CAMPAIGN], daily=DAILY, groups=GROUPS)
    connect_ml(db)
    run = sync()
    assert run.status == "ok" and run.counts["publicidade"] == 1, run.error
    # cabeçalho de versão exigido pela API e só os endpoints novos
    assert camp.calls[0].request.headers["api-version"] == "2"
    assert day.calls[0].request.url.params["aggregation_type"] == "DAILY"
    assert all(c.request.method == "GET" for c in respx.calls)
    assert not any("/product_ads/items" in str(c.request.url) or "/ads/search" in str(c.request.url)
                   for c in respx.calls)  # endpoints desligados em 27/05/2026
    c = db.scalar(select(AdCampaign))
    assert (c.name, c.cost_cents, c.revenue_cents, c.sales, c.budget_cents) == ("Pastilhas", 12050, 90375, 11, 3000)
    assert sorted(d.cost_cents for d in db.scalars(select(AdDaily))) == [1000, 2000]
    # 1ª vez: 90 dias de histórico
    assert day.calls[0].request.url.params["date_from"] == (TODAY - timedelta(days=89)).isoformat()
    # anúncio que gastou R$ 45 sem vender vira alerta
    alert = db.scalar(select(Alert).where(Alert.source == "publicidade", Alert.status == AlertStatus.OPEN))
    assert "R$ 45,00" in alert.title and "Retentor" in alert.detail
    assert db.scalar(select(AdGroup).where(AdGroup.external_id == "MLB456")).revenue_cents == 70000
    # resumo da tela: ROAS e investimento do período
    s = ads_summary(db, 7)
    assert s.cost == 3000 and s.revenue == 15000 and round(s.roas, 1) == 5.0 and s.wasting == 1
    # DRE do mês: investimento entra sozinho como despesa
    if (TODAY - timedelta(days=1)).month == TODAY.month:
        assert month_dre(db, TODAY).expenses[ADS_CATEGORY] == 3000
    # 2ª leitura logo depois: espera 3 horas (os números só mudam 1 vez por dia)
    calls = len(camp.calls)
    sync()
    assert len(camp.calls) == calls


@respx.mock
def test_conta_sem_product_ads_so_avisa(db, ml_env, logged_client):  # noqa: F811
    ml_fake.install(respx)  # o padrão do Mercado Livre simulado: 404 "No permissions found"
    connect_ml(db)
    assert sync().status == "ok"
    db.expire_all()
    account = db.scalar(select(IntegrationAccount).where(IntegrationAccount.provider == "ml",
                                                         IntegrationAccount.is_demo.is_(False)))
    assert account.extra["ads_status"] == "sem_ads"
    page = logged_client.get("/publicidade")
    assert page.status_code == 200 and "não tem o Product Ads" in page.text and "style=" not in page.text


@respx.mock
def test_tela_com_dados(db, ml_env, logged_client):  # noqa: F811
    ml_fake.install(respx)
    _ads(respx, campaigns=[CAMPAIGN], daily=DAILY, groups=GROUPS)
    connect_ml(db)
    sync()
    page = logged_client.get("/publicidade?periodo=7").text
    assert "Pastilhas" in page and "Gastando sem vender" in page and "5,0x" in page


def test_quem_nao_ve_financeiro_nao_abre(client, db, user):
    from tests.conftest import login
    from tests.test_permissions import _add
    _add(db, "at@loja.com", "atendimento")
    login(client, "at@loja.com")
    assert client.get("/publicidade", follow_redirects=False).status_code in (303, 403)


@respx.mock
def test_anuncio_repetido_na_lista_nao_derruba_a_leitura(db, ml_env):  # noqa: F811
    """O Mercado Livre devolveu o mesmo Ad Group duas vezes (visto em 27/11/2026): grava uma vez só."""
    ml_fake.install(respx)
    _ads(respx, campaigns=[CAMPAIGN], daily=DAILY, groups=GROUPS + [GROUPS[0]])
    connect_ml(db)
    run = sync()
    assert run.status == "ok", run.error
    assert db.scalar(select(func.count()).select_from(AdGroup)) == 2


@respx.mock
def test_erro_inesperado_na_publicidade_nao_derruba_o_mercado_livre(db, ml_env, monkeypatch):  # noqa: F811
    from app.sync import ml_ads
    ml_fake.install(respx)
    _ads(respx, campaigns=[CAMPAIGN], daily=DAILY, groups=GROUPS)
    connect_ml(db)

    def boom(*_a, **_k):
        raise RuntimeError("falha inesperada")
    monkeypatch.setattr(ml_ads, "_alerts", boom)
    run = sync()
    assert run.status == "ok" and run.counts["publicidade"] == 0  # perguntas, vendas etc. seguiram normais
    db.expire_all()
    account = db.scalar(select(IntegrationAccount).where(IntegrationAccount.provider == "ml",
                                                         IntegrationAccount.is_demo.is_(False)))
    assert account.extra["ads_status"] == "erro" and "RuntimeError" in account.extra["ads_error"]
