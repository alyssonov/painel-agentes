"""Visão geral com os dados de simulação."""

import pytest
from sqlalchemy import func, select

from app.fake_data.seed import SeedRefused, seed_demo
from app.models import IntegrationAccount, Order, Product
from app.models.enums import Channel, Provider
from app.services import dashboard


def test_visao_geral_mostra_simulacao(logged_client):
    html = logged_client.get("/").text
    assert "MODO SIMULAÇÃO" in html
    for texto in ("Faturamento hoje", "Margem média real", "Perguntas respondidas", "Reputação",
                  "O que pede atenção", "Agentes agora", "Pedidos de hoje",
                  "A separar", "Embalado", "Enviado", "Com problema"):
        assert texto in html


def test_filtro_por_marketplace(logged_client):
    html = logged_client.get("/parciais/pedidos?canal=shopee").text
    assert "chip-shopee" in html
    assert "chip-ml" not in html
    html_ml = logged_client.get("/parciais/pedidos?canal=ml").text
    assert "chip-shopee" not in html_ml


def test_filtro_invalido_mostra_todos(db):
    seed_demo(db, rng_seed=1)
    todos = sum(len(c.orders) for c in dashboard.kanban(db))
    assert sum(len(c.orders) for c in dashboard.kanban(db, "xyz")) == todos


def test_secao_futura_mostra_aviso(logged_client):
    r = logged_client.get("/anuncios")
    assert r.status_code == 200
    assert "Chega na fase 7" in r.text


def test_numeros_da_visao_geral(db):
    seed_demo(db, rng_seed=1)
    ov = dashboard.overview(db)
    assert ov.orders_today >= 6
    assert ov.revenue_today_cents > 0
    assert ov.avg_margin_pct is not None
    assert ov.loss_orders >= 1  # a simulação inclui uma venda com prejuízo
    assert ov.alerts[0].severity == "critico"  # urgentes primeiro
    from app.agents.registry import AGENTS
    assert len(ov.agents) == len(AGENTS)


def test_simulacao_e_idempotente(db):
    seed_demo(db, rng_seed=1)
    seed_demo(db, rng_seed=1)
    assert db.scalar(select(func.count()).select_from(Product)) == 18
    counts = db.execute(select(Order.channel, func.count()).group_by(Order.channel)).all()
    assert {c for c, _ in counts} == {Channel.ML, Channel.SHOPEE}


def test_simulacao_recusada_fora_do_dry_run(db, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("DRY_RUN", "false")
    get_settings.cache_clear()
    with pytest.raises(SeedRefused):
        seed_demo(db)
    get_settings.cache_clear()


def test_simulacao_recusada_com_conta_real(db):
    db.add(IntegrationAccount(provider=Provider.OLIST, account_name="Real", access_token_enc="abc"))
    db.commit()
    with pytest.raises(SeedRefused):
        seed_demo(db)


def test_simulacao_nao_apaga_dados_reais(db):
    db.add(Product(sku="REAL-1", name="Produto real", cost_cents=100, price_cents=200))
    db.commit()
    seed_demo(db, rng_seed=1)
    assert db.scalar(select(Product).where(Product.sku == "REAL-1")) is not None
