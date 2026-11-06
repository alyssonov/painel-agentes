"""Mercado Livre: autorização com PKCE, leitura de perguntas, reputação e taxas."""

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from sqlalchemy import select

from app.config import get_settings
from app.core.db import get_sessionmaker
from app.integrations import oauth
from app.integrations.errors import AuthExpired
from app.integrations.mercadolivre import auth as ml_auth
from app.integrations.mercadolivre.client import MLClient
from app.integrations.mercadolivre.mapping import order_fee_cents, question_status, reputation_label
from app.integrations.tokens import TokenSet, read_access_token, read_refresh_token, store_tokens
from app.models import IntegrationAccount, MarketplaceFee, Order, Question
from app.models.enums import Channel, IntegrationStatus, Provider, QuestionStatus, SyncStatus
from app.sync.ml_sync import run_ml_sync
from tests import ml_fake
from tests.conftest import csrf_from


@pytest.fixture
def ml_env(monkeypatch):
    monkeypatch.setenv("ML_CLIENT_ID", "4280407418569081")
    monkeypatch.setenv("ML_CLIENT_SECRET", "segredo-ml")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def connect_ml(db, expires_in=21600):
    account = IntegrationAccount(provider=Provider.ML, account_name="LOJA", external_id="123456",
                                 is_demo=False, status=IntegrationStatus.OK, extra={})
    store_tokens(account, TokenSet("APP_USR-atual", "TG-atual", expires_in))
    db.add(account)
    db.commit()
    return account


def sync():
    factory = lambda: MLClient(ml_auth.MLTokenProvider(get_sessionmaker()), sleep=lambda _s: None)  # noqa: E731
    return run_ml_sync(get_sessionmaker(), factory)


# --- Regras e conversões -------------------------------------------------------

def test_cliente_do_ml_so_le():
    public = [n for n in dir(MLClient) if not n.startswith("_") and callable(getattr(MLClient, n))]
    assert all(n.startswith(("get_", "iter_")) for n in public), public


def test_pkce_s256():
    verifier, challenge = oauth.new_pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == expected


def test_reputacao():
    assert reputation_label(ml_fake.ME) == "MercadoLíder Gold"
    assert reputation_label({"seller_reputation": {"level_id": "3_yellow"}}) == "Reputação amarela"
    assert reputation_label({}) == "Sem reputação ainda"


def test_situacao_da_pergunta():
    assert question_status("UNANSWERED") == QuestionStatus.NEW
    assert question_status("ANSWERED") == QuestionStatus.ANSWERED
    assert question_status("BANNED") == QuestionStatus.CLOSED


def test_taxa_do_pedido():
    assert order_fee_cents({"order_items": [{"quantity": 2, "sale_fee": 10.49}]}) == 2098


# --- Autorização ----------------------------------------------------------------

def test_endereco_de_autorizacao(ml_env):
    q = parse_qs(urlparse(ml_auth.authorize_url("st", "ch")).query)
    assert q["client_id"] == ["4280407418569081"]
    assert q["redirect_uri"] == ["https://alyssonov.github.io/painel-retorno/mercadolivre/"]
    assert q["code_challenge"] == ["ch"] and q["code_challenge_method"] == ["S256"]
    assert q["state"] == ["st"]


@respx.mock
def test_troca_do_codigo_envia_code_verifier(ml_env):
    routes = ml_fake.install(respx.mock)
    tokens, body = ml_auth.exchange_code("TG-code", "verificador")
    sent = parse_qs(routes["token"].calls[0].request.content.decode())
    assert sent["code_verifier"] == ["verificador"] and sent["grant_type"] == ["authorization_code"]
    assert tokens.access_token == "APP_USR-novo" and body["user_id"] == 123456


@respx.mock
def test_renovacao_guarda_o_refresh_token_novo(db, ml_env):
    connect_ml(db, expires_in=60)
    routes = ml_fake.install(respx.mock)
    assert ml_auth.MLTokenProvider(get_sessionmaker()).get_token() == "APP_USR-novo"
    assert parse_qs(routes["token"].calls[0].request.content.decode())["refresh_token"] == ["TG-atual"]
    db.expire_all()
    account = db.scalar(select(IntegrationAccount))
    assert read_refresh_token(account) == "TG-novo"  # uso único: guarda sempre o último
    assert read_access_token(account) == "APP_USR-novo"


@respx.mock
def test_refresh_recusado_pede_reconexao(db, ml_env):
    connect_ml(db, expires_in=60)
    respx.post(ml_auth.TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(AuthExpired):
        ml_auth.MLTokenProvider(get_sessionmaker()).get_token()


# --- Sincronização ---------------------------------------------------------------

@respx.mock
def test_sincronizacao_le_reputacao_perguntas_e_taxas(db, ml_env):
    connect_ml(db)
    # Pedidos do Olist: um simples e um que veio de um pacote.
    from datetime import datetime
    for ref in ("2000001", "3000001"):
        db.add(Order(channel=Channel.ML, external_id=ref, olist_id=f"o{ref}", customer_short_name="A",
                     ordered_at=datetime(2026, 11, 6, 13), items_total_cents=10000))
    db.commit()
    ml_fake.install(respx.mock)

    run = sync()
    assert run.status == SyncStatus.OK, run.error

    account = db.scalar(select(IntegrationAccount))
    db.refresh(account)
    assert account.account_name == "LOJATESTE"
    assert account.extra["reputation_label"] == "MercadoLíder Gold"

    questions = {q.external_id: q for q in db.scalars(select(Question))}
    assert questions["9001"].status == QuestionStatus.NEW
    assert questions["9001"].listing_title == "Caneca Térmica 500ml"
    assert questions["9002"].status == QuestionStatus.ANSWERED and questions["9002"].answer_text == "Sim, 90 dias."
    assert questions["9003"].status == QuestionStatus.CLOSED
    assert "8000" not in questions  # antiga demais: fica de fora

    orders = {o.external_id: o for o in db.scalars(select(Order))}
    assert orders["2000001"].marketplace_fee_cents == 2098 and orders["2000001"].fees_known
    assert orders["3000001"].marketplace_fee_cents == 1250  # 5,00 + 7,50 do pacote
    assert db.query(MarketplaceFee).count() >= 3


@respx.mock
def test_pergunta_na_fila_do_painel_nao_volta_para_nova(db, ml_env):
    connect_ml(db)
    ml_fake.install(respx.mock)
    sync()
    q = db.scalar(select(Question).where(Question.external_id == "9001"))
    q.status = QuestionStatus.PROPOSED
    db.commit()
    sync()
    db.refresh(q)
    assert q.status == QuestionStatus.PROPOSED


@respx.mock
def test_taxa_lida_antes_do_pedido_chegar_no_olist(db, ml_env):
    """O pedido chega no Olist depois: a taxa já guardada é aplicada na importação."""
    connect_ml(db)
    ml_fake.install(respx.mock)
    sync()
    from app.sync.olist_sync import upsert_order
    order = upsert_order(db, {"id": 77}, {
        "id": 77, "situacao": 3, "ecommerce": {"nome": "Mercado Livre", "numeroPedidoEcommerce": "2000001"},
        "dataCriacao": "2026-11-06 10:00:00", "cliente": {"nome": "Ana Lima"}, "valorTotalProdutos": 100,
        "itens": []}, {})
    db.commit()
    assert order.marketplace_fee_cents == 2098 and order.fees_known


@respx.mock
def test_falha_vira_alerta(db, ml_env):
    connect_ml(db)
    respx.get(f"{ml_fake.BASE_URL}/users/me").mock(return_value=httpx.Response(503))
    run = sync()
    assert run.status == SyncStatus.ERROR
    from app.models import Alert
    alert = db.scalar(select(Alert).where(Alert.source == "ml"))
    assert alert is not None and "Mercado Livre" in alert.title


# --- Telas -------------------------------------------------------------------------

def test_sem_chaves_mostra_instrucoes(logged_client):
    html = logged_client.get("/integracoes").text
    assert "ML_CLIENT_ID" in html


@respx.mock
def test_conexao_completa(logged_client, ml_env, db, monkeypatch):
    synced = []
    monkeypatch.setattr("app.web.routes.integrations._ml_sync_in_background", lambda: synced.append(1))
    routes = ml_fake.install(respx.mock)
    r = logged_client.get("/integracoes/mercadolivre/conectar", follow_redirects=False)
    url = urlparse(r.headers["location"])
    assert url.netloc == "auth.mercadolivre.com.br"
    q = parse_qs(url.query)
    r = logged_client.get(f"/integracoes/mercadolivre/retorno?code=TG-code&state={q['state'][0]}")
    assert "Mercado Livre conectado" in r.text
    # O verificador PKCE enviado bate com o desafio mostrado ao Mercado Livre.
    verifier = parse_qs(routes["token"].calls[0].request.content.decode())["code_verifier"][0]
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert q["code_challenge"] == [expected]
    db.expire_all()
    account = db.scalar(select(IntegrationAccount).where(IntegrationAccount.provider == Provider.ML,
                                                         IntegrationAccount.is_demo.is_(False)))
    assert account.external_id == "123456" and read_access_token(account) == "APP_USR-novo"
    assert "APP_USR-novo" not in account.access_token_enc
    assert synced == [1]


def test_retorno_com_state_errado(logged_client, ml_env):
    logged_client.get("/integracoes/mercadolivre/conectar", follow_redirects=False)
    r = logged_client.get("/integracoes/mercadolivre/retorno?code=x&state=forjado")
    assert "Não foi possível confirmar" in r.text


def test_desconectar(logged_client, ml_env, db):
    connect_ml(db)
    token = csrf_from(logged_client.get("/integracoes").text)
    logged_client.post("/integracoes/mercadolivre/desconectar", data={"csrf_token": token})
    db.expire_all()
    account = db.scalar(select(IntegrationAccount).where(IntegrationAccount.provider == Provider.ML,
                                                         IntegrationAccount.is_demo.is_(False)))
    assert account.access_token_enc is None and account.status == IntegrationStatus.DISCONNECTED


@respx.mock
def test_pergunta_respondida_direto_no_ml_sai_da_fila(db, ml_env):
    """Pergunta antiga que estava aberta no painel e foi respondida direto no Mercado Livre."""
    from datetime import datetime

    from app.core import actions
    from app.models import Action
    from app.models.enums import ActionStatus
    connect_ml(db)
    old = Question(channel=Channel.ML, external_id="7777", listing_external_id="MLB1", text="Antiga",
                   asked_at=datetime(2026, 10, 1, 12), status=QuestionStatus.PROPOSED)
    db.add(old)
    db.commit()
    action = actions.propose(db, agent_slug="perguntas", type="responder_pergunta", channel=Channel.ML,
                             target_type="pergunta", target_id=old.id, payload={"text": "x"})
    db.commit()
    ml_fake.install(respx.mock)
    route = respx.get(f"{ml_fake.BASE_URL}/questions/7777").mock(return_value=httpx.Response(200, json={
        "id": 7777, "item_id": "MLB1", "status": "ANSWERED", "text": "Antiga",
        "date_created": "2026-10-01T09:00:00.000-03:00",
        "answer": {"text": "Respondi pelo app", "date_created": "2026-11-06T08:00:00.000-03:00"}}))
    assert sync().status == SyncStatus.OK
    assert route.call_count == 1
    db.expire_all()
    assert db.get(Question, old.id).status == QuestionStatus.ANSWERED
    assert db.get(Question, old.id).answer_text == "Respondi pelo app"
    assert db.get(Action, action.id).status == ActionStatus.REJECTED


@respx.mock
def test_pergunta_em_analise_volta_quando_liberada(db, ml_env):
    """O Mercado Livre segura algumas perguntas 'em análise'; quando libera, ela entra na fila."""
    connect_ml(db)
    routes = ml_fake.install(respx.mock)
    review = {"id": 9100, "item_id": "MLB1", "status": "UNDER_REVIEW", "text": "Pergunta em análise",
              "date_created": "2026-11-06T11:00:00.000-03:00"}

    def questions(request, state={"status": "UNDER_REVIEW"}):
        q = {**review, "status": state["status"]}
        wanted = request.url.params.get("status")
        data = [q] if not wanted or wanted == q["status"] else []
        return httpx.Response(200, json={"total": len(data), "questions": data})

    routes["questions"].mock(side_effect=questions)
    respx.get(f"{ml_fake.BASE_URL}/questions/9100").mock(
        side_effect=lambda r: httpx.Response(200, json={**review, "status": questions.__defaults__[0]["status"]}))
    sync()
    q = db.scalar(select(Question).where(Question.external_id == "9100"))
    assert q.status == QuestionStatus.REVIEW
    questions.__defaults__[0]["status"] = "UNANSWERED"
    sync()
    db.refresh(q)
    assert q.status == QuestionStatus.NEW
