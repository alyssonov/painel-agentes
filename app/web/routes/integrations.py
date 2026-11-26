"""Contas conectadas: conectar, sincronizar e desconectar o Olist (somente leitura)."""

import hmac
import logging
import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import audit
from app.core.db import get_db, get_sessionmaker, utcnow
from app.core.timeutil import to_local
from app.fake_data.seed import clear_demo
from app.integrations.errors import IntegrationError
from app.integrations import oauth
from app.integrations.mercadolivre import auth as ml_auth
from app.integrations.olist import auth as olist_auth
from app.integrations.shopee import auth as shopee_auth
from app.integrations.olist.client import OlistClient
from app.integrations.tokens import clear_tokens, store_tokens
from app.models import IntegrationAccount, Product, SyncRun
from app.models.enums import IntegrationStatus, Provider
from app.models.user import User
from app.services.store_settings import get_setting, set_setting
from app.sync.ml_sync import run_ml_sync
from app.sync.olist_sync import run_olist_sync
from app.sync.shopee_sync import run_shopee_sync
from app.web.auth import current_user, verify_csrf
from app.web.templating import flash, render

logger = logging.getLogger("painel.integracoes")
router = APIRouter(prefix="/integracoes")

_STATE_KEY = "olist_oauth_state"
_ML_STATE_KEY = "ml_oauth_state"
_ML_VERIFIER_KEY = "ml_oauth_verifier"
_SHOPEE_STATE_KEY = "shopee_oauth_state"


def _sync_in_background() -> None:
    """Roda a sincronização fora da requisição (o painel não fica esperando)."""
    try:
        run_olist_sync(get_sessionmaker())
    except Exception:  # noqa: BLE001
        logger.exception("Falha na sincronização em segundo plano")


def _ml_sync_in_background() -> None:
    try:
        run_ml_sync(get_sessionmaker())
    except Exception:  # noqa: BLE001
        logger.exception("Falha na sincronização do Mercado Livre em segundo plano")


def _last_run(db: Session, provider: str):
    return db.scalar(select(SyncRun).where(SyncRun.provider == provider).order_by(SyncRun.id.desc()).limit(1))


@router.get("")
def integrations_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    settings = get_settings()
    account = olist_auth.get_olist_account(db)
    connected = bool(account and account.access_token_enc)
    last_run = db.scalar(select(SyncRun).where(SyncRun.provider == Provider.OLIST)
                         .order_by(SyncRun.id.desc()).limit(1))
    products = db.query(Product).filter(Product.is_demo.is_(False)).count() if connected else 0
    return render(request, "integrations.html", {
        "olist_configured": settings.olist_configured,
        "redirect_uri": settings.olist_redirect_uri,
        "account": account,
        "connected": connected,
        "last_run": last_run,
        "last_run_label": to_local(last_run.started_at).strftime("%d/%m às %H:%M") if last_run else None,
        "products_count": products,
        "sync_minutes": settings.olist_sync_minutes,
        "ml": _ml_context(db),
        "shopee": _shopee_context(db),
        "onix": _onix_context(db),
        "tax_rate_pct": float(get_setting(db, "tax_rate_pct") or 0),
        "tax_rate_is_demo": bool(get_setting(db, "tax_rate_is_demo")),
    }, db=db)


@router.get("/olist/conectar")
def olist_connect(request: Request, user: User = Depends(current_user)):
    """Leva para a tela de autorização do Olist."""
    if not get_settings().olist_configured:
        flash(request, "Faltam as chaves do aplicativo do Olist no arquivo .env.", "erro")
        return RedirectResponse("/integracoes", status_code=303)
    state = secrets.token_urlsafe(32)
    request.session[_STATE_KEY] = state
    return RedirectResponse(olist_auth.authorize_url(state), status_code=303)


@router.get("/olist/retorno")
def olist_callback(request: Request, background: BackgroundTasks, code: str | None = None,
                   state: str | None = None, error: str | None = None,
                   db: Session = Depends(get_db), user: User = Depends(current_user)):
    """O Olist volta para cá depois que a pessoa autoriza (ou recusa)."""
    expected = request.session.pop(_STATE_KEY, None)
    if not expected or not state or not hmac.compare_digest(expected, state):
        audit.record(db, actor=f"user:{user.id}", event="olist.conexao.recusada", details={"motivo": "state"})
        db.commit()
        flash(request, "Não foi possível confirmar a conexão. Clique em “Conectar” de novo.", "erro")
        return RedirectResponse("/integracoes", status_code=303)
    if error or not code:
        flash(request, "A autorização no Olist foi cancelada.", "erro")
        return RedirectResponse("/integracoes", status_code=303)

    try:
        tokens = olist_auth.exchange_code(code)
    except IntegrationError as exc:
        audit.record(db, actor=f"user:{user.id}", event="olist.conexao.falhou", details={"erro": str(exc)})
        db.commit()
        flash(request, str(exc), "erro")
        return RedirectResponse("/integracoes", status_code=303)

    account = olist_auth.get_olist_account(db)
    if account is None:
        account = IntegrationAccount(provider=Provider.OLIST, account_name="Olist ERP", is_demo=False, extra={})
        db.add(account)
    store_tokens(account, tokens)
    account.status = IntegrationStatus.OK
    account.last_error = None
    # A conta mudou: recomeça a leitura do zero (evita misturar dados de outra empresa).
    account.extra = {}
    account.last_ok_at = None
    db.flush()

    # Nome da empresa, para mostrar no painel.
    try:
        with OlistClient(olist_auth.OlistTokenProvider(get_sessionmaker())) as client:
            db.commit()  # o cliente lê o token do banco
            info = client.get_company_info()
        account.account_name = info.get("fantasia") or info.get("razaoSocial") or "Olist ERP"
        account.external_id = info.get("cpfCnpj") or account.external_id
    except IntegrationError as exc:
        logger.warning("Não foi possível ler /info do Olist: %s", exc)

    # Dados reais chegando: sai a loja fictícia da simulação.
    clear_demo(db, pause_agents=True)
    audit.record(db, actor=f"user:{user.id}", event="olist.conectado", target_type="integracao",
                 target_id="olist", details={"empresa": account.account_name})
    db.commit()

    background.add_task(_sync_in_background)
    flash(request, "Olist conectado! Os dados estão sendo importados — isso pode levar alguns minutos.")
    return RedirectResponse("/integracoes", status_code=303)


@router.post("/olist/sincronizar", dependencies=[Depends(verify_csrf)])
def olist_sync_now(request: Request, background: BackgroundTasks, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    audit.record(db, actor=f"user:{user.id}", event="olist.sincronizacao.manual")
    db.commit()
    background.add_task(_sync_in_background)
    flash(request, "Atualização iniciada. Recarregue a página em instantes para ver o resultado.")
    return RedirectResponse("/integracoes", status_code=303)


@router.post("/olist/desconectar", dependencies=[Depends(verify_csrf)])
def olist_disconnect(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Apaga os tokens. Os dados já importados continuam no painel."""
    account = olist_auth.get_olist_account(db)
    if account:
        clear_tokens(account)
        account.status = IntegrationStatus.DISCONNECTED
        audit.record(db, actor=f"user:{user.id}", event="olist.desconectado", target_type="integracao",
                     target_id="olist")
        db.commit()
    flash(request, "Olist desconectado. Os dados já importados continuam no painel.")
    return RedirectResponse("/integracoes", status_code=303)


@router.post("/imposto", dependencies=[Depends(verify_csrf)])
def save_tax_rate(request: Request, tax_rate_pct: str = Form(...), db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    """Alíquota efetiva de impostos sobre a venda (informada pela contabilidade)."""
    try:
        value = float(tax_rate_pct.replace(",", ".").replace("%", "").strip())
    except ValueError:
        value = -1
    if not 0 <= value <= 60:
        flash(request, "Informe a alíquota como um número entre 0 e 60 (ex.: 14,25).", "erro")
        return RedirectResponse("/integracoes", status_code=303)
    old = get_setting(db, "tax_rate_pct")
    set_setting(db, "tax_rate_pct", value)
    set_setting(db, "tax_rate_is_demo", False)
    audit.record(db, actor=f"user:{user.id}", event="loja.aliquota_alterada",
                 details=audit.changes({"aliquota_pct": old}, {"aliquota_pct": value}))
    db.commit()
    flash(request, f"Alíquota de impostos salva: {str(value).replace('.', ',')}%.")
    return RedirectResponse("/integracoes", status_code=303)


# --- Mercado Livre ---------------------------------------------------------------

def _ml_context(db: Session) -> dict:
    settings = get_settings()
    account = ml_auth.get_ml_account(db)
    last_run = _last_run(db, Provider.ML)
    return {
        "configured": settings.ml_configured,
        "redirect_uri": settings.ml_redirect_uri,
        "account": account,
        "connected": bool(account and account.access_token_enc),
        "last_run": last_run,
        "last_run_label": to_local(last_run.started_at).strftime("%d/%m às %H:%M") if last_run else None,
        "sync_minutes": settings.ml_sync_minutes,
    }


@router.get("/mercadolivre/conectar")
def ml_connect(request: Request, user: User = Depends(current_user)):
    """Leva para a tela de autorização do Mercado Livre (com PKCE)."""
    if not get_settings().ml_configured:
        flash(request, "Faltam as chaves do aplicativo do Mercado Livre no arquivo .env.", "erro")
        return RedirectResponse("/integracoes", status_code=303)
    state = secrets.token_urlsafe(32)
    verifier, challenge = oauth.new_pkce_pair()
    request.session[_ML_STATE_KEY] = state
    request.session[_ML_VERIFIER_KEY] = verifier
    return RedirectResponse(ml_auth.authorize_url(state, challenge), status_code=303)


@router.get("/mercadolivre/retorno")
def ml_callback(request: Request, background: BackgroundTasks, code: str | None = None,
                state: str | None = None, error: str | None = None,
                db: Session = Depends(get_db), user: User = Depends(current_user)):
    """O Mercado Livre volta para cá (passando pela página de repasse) depois da autorização."""
    expected = request.session.pop(_ML_STATE_KEY, None)
    verifier = request.session.pop(_ML_VERIFIER_KEY, None)
    if not expected or not state or not verifier or not hmac.compare_digest(expected, state):
        audit.record(db, actor=f"user:{user.id}", event="ml.conexao.recusada", details={"motivo": "state"})
        db.commit()
        flash(request, "Não foi possível confirmar a conexão. Clique em “Conectar” de novo.", "erro")
        return RedirectResponse("/integracoes", status_code=303)
    if error or not code:
        flash(request, "A autorização no Mercado Livre foi cancelada.", "erro")
        return RedirectResponse("/integracoes", status_code=303)

    try:
        tokens, body = ml_auth.exchange_code(code, verifier)
    except IntegrationError as exc:
        audit.record(db, actor=f"user:{user.id}", event="ml.conexao.falhou", details={"erro": str(exc)})
        db.commit()
        flash(request, str(exc), "erro")
        return RedirectResponse("/integracoes", status_code=303)

    seller_id = str(body.get("user_id") or "")
    account = ml_auth.get_ml_account(db)
    if account is None:
        account = IntegrationAccount(provider=Provider.ML, account_name="Mercado Livre", is_demo=False, extra={})
        db.add(account)
    if account.external_id and seller_id and account.external_id != seller_id:
        account.extra = {}  # outra conta do Mercado Livre: recomeça a leitura do zero
        account.last_ok_at = None
    store_tokens(account, tokens)
    account.external_id = seller_id or account.external_id
    account.status = IntegrationStatus.OK
    account.last_error = None
    clear_demo(db, pause_agents=True)
    audit.record(db, actor=f"user:{user.id}", event="ml.conectado", target_type="integracao",
                 target_id="ml", details={"conta": seller_id})
    db.commit()

    background.add_task(_ml_sync_in_background)
    flash(request, "Mercado Livre conectado! Perguntas, reputação e taxas estão sendo importadas.")
    return RedirectResponse("/integracoes", status_code=303)


@router.post("/mercadolivre/sincronizar", dependencies=[Depends(verify_csrf)])
def ml_sync_now(request: Request, background: BackgroundTasks, db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    audit.record(db, actor=f"user:{user.id}", event="ml.sincronizacao.manual")
    db.commit()
    background.add_task(_ml_sync_in_background)
    flash(request, "Atualização do Mercado Livre iniciada. Recarregue a página em instantes.")
    return RedirectResponse("/integracoes", status_code=303)


@router.post("/mercadolivre/desconectar", dependencies=[Depends(verify_csrf)])
def ml_disconnect(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    account = ml_auth.get_ml_account(db)
    if account:
        clear_tokens(account)
        account.status = IntegrationStatus.DISCONNECTED
        audit.record(db, actor=f"user:{user.id}", event="ml.desconectado", target_type="integracao",
                     target_id="ml")
        db.commit()
    flash(request, "Mercado Livre desconectado. Os dados já importados continuam no painel.")
    return RedirectResponse("/integracoes", status_code=303)


# --- Shopee ----------------------------------------------------------------------

def _shopee_context(db: Session) -> dict:
    settings = get_settings()
    account = shopee_auth.get_shopee_account(db)
    last_run = _last_run(db, Provider.SHOPEE)
    return {
        "configured": settings.shopee_configured,
        "redirect_uri": settings.shopee_redirect_uri,
        "sandbox": settings.shopee_sandbox,
        "account": account,
        "connected": bool(account and account.access_token_enc),
        "last_ok_label": to_local(account.last_ok_at).strftime("%d/%m às %H:%M")
        if account and account.last_ok_at else None,
        "valid_until": to_local(account.refresh_expires_at).strftime("%d/%m/%Y")
        if account and account.refresh_expires_at else None,
        "last_run": last_run,
        "last_run_label": to_local(last_run.started_at).strftime("%d/%m às %H:%M") if last_run else None,
        "sync_minutes": settings.shopee_sync_minutes,
        "auth_expires": _shopee_auth_expires(account),
    }


def _shopee_auth_expires(account) -> str | None:
    """Data em que a autorização da loja vence (a Shopee pede para conectar de novo)."""
    value = ((account.extra or {}) if account else {}).get("auth_expires_at")
    if not value:
        return None
    from datetime import datetime
    return to_local(datetime.fromisoformat(value)).strftime("%d/%m/%Y")


def _shopee_sync_in_background() -> None:
    try:
        run_shopee_sync(get_sessionmaker())
    except Exception:  # noqa: BLE001
        logger.exception("Falha na sincronização da Shopee em segundo plano")


@router.post("/shopee/sincronizar", dependencies=[Depends(verify_csrf)])
def shopee_sync_now(request: Request, background: BackgroundTasks, db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    audit.record(db, actor=f"user:{user.id}", event="shopee.sincronizacao.manual")
    db.commit()
    background.add_task(_shopee_sync_in_background)
    flash(request, "Atualização da Shopee iniciada. Recarregue a página em instantes.")
    return RedirectResponse("/integracoes", status_code=303)


@router.get("/shopee/conectar")
def shopee_connect(request: Request, user: User = Depends(current_user)):
    """Leva para a tela de autorização da Shopee."""
    if not get_settings().shopee_configured:
        flash(request, "Faltam o Partner ID e a Partner Key da Shopee no arquivo .env.", "erro")
        return RedirectResponse("/integracoes", status_code=303)
    state = secrets.token_urlsafe(32)
    request.session[_SHOPEE_STATE_KEY] = state
    return RedirectResponse(shopee_auth.authorize_url(state), status_code=303)


def _int_or_none(value: str | None) -> int | None:
    return int(value) if value and value.strip().isdigit() else None


@router.get("/shopee/retorno")
def shopee_callback(request: Request, background: BackgroundTasks, code: str | None = None, shop_id: str | None = None,
                    main_account_id: str | None = None, state: str | None = None,
                    db: Session = Depends(get_db), user: User = Depends(current_user)):
    """A Shopee volta para cá (passando pela página de repasse) depois da autorização."""
    expected = request.session.pop(_SHOPEE_STATE_KEY, None)
    if not expected or not state or not hmac.compare_digest(expected, state):
        audit.record(db, actor=f"user:{user.id}", event="shopee.conexao.recusada", details={"motivo": "state"})
        db.commit()
        flash(request, "Não foi possível confirmar a conexão. Clique em “Conectar” de novo.", "erro")
        return RedirectResponse("/integracoes", status_code=303)
    if not code:
        flash(request, "A autorização na Shopee foi cancelada.", "erro")
        return RedirectResponse("/integracoes", status_code=303)

    try:
        tokens, shop = shopee_auth.exchange_code(code, shop_id=_int_or_none(shop_id),
                                                 main_account_id=_int_or_none(main_account_id))
    except IntegrationError as exc:
        audit.record(db, actor=f"user:{user.id}", event="shopee.conexao.falhou", details={"erro": str(exc)})
        db.commit()
        flash(request, str(exc), "erro")
        return RedirectResponse("/integracoes", status_code=303)

    account = shopee_auth.get_shopee_account(db)
    if account is None:
        account = IntegrationAccount(provider=Provider.SHOPEE, account_name="Shopee", is_demo=False, extra={})
        db.add(account)
    if account.external_id and account.external_id != str(shop):
        account.extra = {}  # outra loja: recomeça a leitura do zero
    store_tokens(account, tokens)
    account.external_id = str(shop)
    account.account_name = f"Loja Shopee nº {shop}"
    account.status = IntegrationStatus.OK
    account.last_error = None
    account.last_ok_at = utcnow()
    clear_demo(db, pause_agents=True)
    audit.record(db, actor=f"user:{user.id}", event="shopee.conectado", target_type="integracao",
                 target_id="shopee", details={"loja": str(shop), "teste": get_settings().shopee_sandbox})
    db.commit()
    background.add_task(_shopee_sync_in_background)
    flash(request, "Shopee conectada! Os pedidos, prazos de envio e taxas estão sendo lidos — "
                   "isso pode levar alguns minutos.")
    return RedirectResponse("/integracoes", status_code=303)


@router.post("/shopee/desconectar", dependencies=[Depends(verify_csrf)])
def shopee_disconnect(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    account = shopee_auth.get_shopee_account(db)
    if account:
        clear_tokens(account)
        account.status = IntegrationStatus.DISCONNECTED
        audit.record(db, actor=f"user:{user.id}", event="shopee.desconectado", target_type="integracao",
                     target_id="shopee")
        db.commit()
    flash(request, "Shopee desconectada. Para cancelar também do lado da Shopee: Central do Vendedor → "
                   "Parceiros da plataforma.")
    return RedirectResponse("/integracoes", status_code=303)


# --- Onix (loja física) --------------------------------------------------------------

def _onix_context(db: Session) -> dict:
    from urllib.parse import urlsplit
    settings = get_settings()
    last_run = _last_run(db, "onix")
    host = urlsplit(settings.onix_api_url).hostname or "" if settings.onix_configured else ""
    local = host in ("localhost", "127.0.0.1") or host.startswith(("192.168.", "10.", "172."))
    return {
        "configured": settings.onix_configured,
        "insecure": settings.onix_configured and settings.onix_api_url.startswith("http://") and not local,
        "cost_field": settings.onix_cost_field, "hours": settings.onix_sync_hours,
        "last_run": last_run,
        "last_run_label": to_local(last_run.started_at).strftime("%d/%m às %H:%M") if last_run else None,
    }


def _onix_sync_in_background() -> None:
    from app.sync.onix_sync import run_onix_sync
    try:
        run_onix_sync(get_sessionmaker())
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao ler o Onix em segundo plano")


@router.post("/onix/sincronizar", dependencies=[Depends(verify_csrf)])
def onix_sync_now(request: Request, background: BackgroundTasks, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    if not get_settings().onix_configured:
        flash(request, "Falta o endereço da API do Onix (ONIX_API_URL no .env).", "erro")
        return RedirectResponse("/integracoes", status_code=303)
    audit.record(db, actor=f"user:{user.id}", event="onix.sincronizacao.manual")
    db.commit()
    background.add_task(_onix_sync_in_background)
    flash(request, "Leitura do Onix iniciada. Pode levar alguns minutos (um produto por vez); recarregue depois.")
    return RedirectResponse("/integracoes", status_code=303)
