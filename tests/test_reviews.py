"""Fase 7 — Agente 6 (Avaliações e devoluções). Só leitura + avisos."""

import json
from datetime import timedelta

import respx
from sqlalchemy import select

from app.agents.registry import ensure_agents
from app.agents.reviews import run_reviews_agent
from app.ai.client import RawResult
from app.core.db import get_sessionmaker, utcnow
from app.models import Agent, Alert, Claim, ClaimReason, ItemRating, Listing, ProductInsight, ProductReview
from app.models.enums import Channel
from app.services.quality import claim_group, product_quality
from tests import ml_fake
from tests.test_ml import connect_ml, ml_env, sync  # noqa: F401 - fixture
from tests.test_phase4 import product, sale


def ai(resumo="A tampa vaza.", calls=None):
    def transport(model, effort, system, user, schema, max_tokens):
        if calls is not None:
            calls.append(user)
        return RawResult(text=json.dumps({"resumo": resumo, "problemas": [
            {"problema": "Tampa vaza", "evidencia": "2 avaliações: \"a tampa vaza\""}],
            "sugestoes": ["Testar a vedação antes de enviar"]}), stop_reason="end_turn",
            input_tokens=900, output_tokens=200)
    return transport


@respx.mock
def test_sincroniza_avaliacoes_e_historico_de_reclamacoes(db, ml_env):
    connect_ml(db)
    prod = product(db, "CAN-01", cost=2000)
    db.add(Listing(channel=Channel.ML, external_id="MLB1", item_id="MLB1", sku="CAN-01", product_id=prod.id,
                   title="Caneca", price_cents=6990, status="active", synced_at=utcnow()))
    db.commit()
    routes = ml_fake.install(respx.mock)
    assert sync().status == "ok"
    rating = db.scalar(select(ItemRating))
    assert rating.average == 4.1 and rating.total == 20 and rating.low == 4 and rating.product_id == prod.id
    reviews = list(db.scalars(select(ProductReview)))
    assert [r.rate for r in reviews] == [2]  # só as negativas
    history = [c.request.url.params.get("range") for c in routes["claims"].calls if "range" in c.request.url.params]
    assert history and history[0].startswith("date_created:after:") and ".000+00:00" in history[0]
    assert db.get(ClaimReason, "PNR9501").detail == "O produto veio com defeito"


def test_qualidade_por_produto(db):
    prod = product(db, "X", cost=1000, price=5000)
    orders = [sale(db, prod, qty=1, days_ago=d) for d in (1, 2, 3, 4)]
    for i, (order, reason, ctype) in enumerate(zip(orders, ["PDD9549", "PDD9550", "PNR1", "CS1"],
                                                   ["mediations", "returns", "mediations", "cancel_purchase"])):
        db.add(Claim(channel=Channel.ML, external_id=f"C{i}", order_ref=order.external_id, status="closed",
                     type=ctype, reason_id=reason, seller_actions=[], opened_at=utcnow(), checked_at=utcnow()))
    db.add(ItemRating(channel=Channel.ML, item_id="MLB1", product_id=prod.id, average=3.8, total=10, low=3,
                      checked_at=utcnow()))
    db.commit()
    (q,) = product_quality(db)
    assert q.units == 4 and q.claims == 3  # cancelamento não conta como problema do produto
    assert q.product_problems == 2 and q.claim_rate == 75.0 and q.rating_average == 3.8
    assert claim_group("PNR1") == "Não recebido"


def test_agente_resume_com_ia_avisa_e_nao_repete_na_mesma_semana(db, ml_env):
    connect_ml(db)
    ensure_agents(db)
    agent = db.scalar(select(Agent).where(Agent.slug == "avaliacoes"))
    agent.enabled = True
    prod = product(db, "Y", cost=1000, price=5000)
    orders = [sale(db, prod, qty=1, days_ago=d) for d in (1, 2)]
    for i, order in enumerate(orders):
        db.add(Claim(channel=Channel.ML, external_id=f"D{i}", order_ref=order.external_id, status="closed",
                     type="mediations", reason_id="PDD9549", seller_actions=[], opened_at=utcnow(),
                     checked_at=utcnow()))
        db.add(ProductReview(channel=Channel.ML, review_id=f"r{i}", item_id="MLB1", product_id=prod.id, rate=1,
                             content="A tampa vaza água", created_at=utcnow() - timedelta(days=i)))
    db.commit()
    calls = []
    run = run_reviews_agent(get_sessionmaker(), transport=ai(calls=calls))
    assert run.status == "ok", run.error
    db.expire_all()
    insight = db.scalar(select(ProductInsight))
    assert insight.summary == "A tampa vaza." and insight.suggestions == ["Testar a vedação antes de enviar"]
    assert "A tampa vaza água" in calls[0] and "PRODUTO: Produto Y" in calls[0]
    alert = db.scalar(select(Alert).where(Alert.source == "avaliacoes"))
    assert "2 reclamação(ões) em 2 vendidos" in alert.title
    run_reviews_agent(get_sessionmaker(), transport=ai(calls=calls))
    assert len(calls) == 1  # mesma semana: não chama a IA de novo


def test_pagina_de_avaliacoes(logged_client, db):
    prod = product(db, "Z", cost=1000, price=5000)
    db.add(ProductReview(channel=Channel.ML, review_id="z1", item_id="MLB1", product_id=prod.id, rate=2,
                         title="Veio torto", content="Chegou com a alça torta.", created_at=utcnow(), is_demo=True))
    prod.is_demo = True
    db.commit()
    html = logged_client.get("/pos-venda/avaliacoes").text
    assert "Avaliações e devoluções" in html and "Chegou com a alça torta." in html and "style=" not in html
    assert "Avaliações e devoluções por produto" in logged_client.get("/pos-venda").text
