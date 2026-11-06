"""Autorização OAuth 2 do Olist ERP.

Documentação oficial consultada em 06/11/2026:
- https://api-docs.erp.olist.com/documentacao/comecando/autenticacao
- https://api-docs.erp.olist.com/documentacao/comecando/criando-um-aplicativo
- https://accounts.tiny.com.br/realms/tiny/.well-known/openid-configuration

Pontos importantes da documentação:
- o token de acesso expira em 4 horas;
- o refresh token dura 1 dia. Se o painel ficar mais de 1 dia sem renovar
  (por exemplo, computador desligado), é preciso conectar de novo;
- as permissões valem a partir do login: se mudarem no aplicativo do Olist,
  é preciso conectar de novo.
"""

import threading
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.core.db import utcnow
from app.integrations.errors import AuthExpired, IntegrationError, NotConnected, TemporaryError
from app.integrations.tokens import TokenSet, read_access_token, read_refresh_token, store_tokens
from app.models.enums import IntegrationStatus, Provider
from app.models.integration import IntegrationAccount

AUTH_URL = "https://accounts.tiny.com.br/realms/tiny/protocol/openid-connect/auth"
TOKEN_URL = "https://accounts.tiny.com.br/realms/tiny/protocol/openid-connect/token"

# Renova o token quando faltar menos que isto para expirar.
REFRESH_MARGIN = timedelta(minutes=10)
TIMEOUT = httpx.Timeout(30.0)

# Evita que duas renovações aconteçam ao mesmo tempo no mesmo processo.
_refresh_lock = threading.Lock()


def authorize_url(state: str) -> str:
    """Endereço da tela de login/autorização do Olist."""
    settings = get_settings()
    query = urlencode({
        "client_id": settings.olist_client_id,
        "redirect_uri": settings.olist_redirect_uri,
        "scope": "openid",
        "response_type": "code",
        "state": state,  # proteção contra pedidos forjados (conferido no retorno)
    })
    return f"{AUTH_URL}?{query}"


def _token_request(data: dict, http: httpx.Client | None = None) -> TokenSet:
    settings = get_settings()
    payload = {"client_id": settings.olist_client_id, "client_secret": settings.olist_client_secret, **data}
    client = http or httpx.Client(timeout=TIMEOUT)
    try:
        response = client.post(TOKEN_URL, data=payload)
    except httpx.HTTPError as exc:
        raise TemporaryError("Não foi possível falar com o Olist agora. Tente de novo em instantes.") from exc
    finally:
        if http is None:
            client.close()
    if response.status_code >= 500:
        raise TemporaryError("O servidor de login do Olist está instável. Tente de novo em instantes.")
    if response.status_code in (400, 401):
        error = _safe_json(response).get("error", "")
        if error == "invalid_grant":
            raise AuthExpired("A autorização do Olist expirou. Clique em “Conectar” para autorizar de novo.")
        if error in ("invalid_client", "unauthorized_client"):
            raise IntegrationError("O Olist recusou as chaves do aplicativo. Confira OLIST_CLIENT_ID e "
                                   "OLIST_CLIENT_SECRET no .env.")
        raise IntegrationError(f"O Olist recusou a autorização ({error or response.status_code}).")
    if response.status_code != 200:
        raise IntegrationError(f"Resposta inesperada do Olist ({response.status_code}).")
    return TokenSet.from_response(response.json())


def exchange_code(code: str, http: httpx.Client | None = None) -> TokenSet:
    """Troca o código recebido no retorno da autorização pelos tokens."""
    return _token_request({
        "grant_type": "authorization_code",
        "redirect_uri": get_settings().olist_redirect_uri,
        "code": code,
    }, http)


def refresh_tokens(refresh_token: str, http: httpx.Client | None = None) -> TokenSet:
    """Gera um token de acesso novo a partir do refresh token."""
    return _token_request({"grant_type": "refresh_token", "refresh_token": refresh_token}, http)


def get_olist_account(db: Session) -> IntegrationAccount | None:
    """Conta real do Olist (ignora a conta fictícia da simulação)."""
    return db.scalar(select(IntegrationAccount).where(
        IntegrationAccount.provider == Provider.OLIST, IntegrationAccount.is_demo.is_(False)))


class OlistTokenProvider:
    """Entrega um token de acesso válido, renovando quando preciso."""

    def __init__(self, session_factory: sessionmaker[Session], http: httpx.Client | None = None) -> None:
        self._session_factory = session_factory
        self._http = http

    def get_token(self, *, force_refresh: bool = False) -> str:
        with self._session_factory() as db:
            account = get_olist_account(db)
            if account is None or not account.access_token_enc:
                raise NotConnected("O Olist ainda não foi conectado.")
            fresh = account.expires_at and account.expires_at - REFRESH_MARGIN > utcnow()
            if fresh and not force_refresh:
                return read_access_token(account)

        with _refresh_lock:
            with self._session_factory() as db:
                # Relê do banco: outro processo (painel ou worker) pode ter renovado agora.
                account = get_olist_account(db)
                if account is None or not account.refresh_token_enc:
                    raise NotConnected("O Olist ainda não foi conectado.")
                if not force_refresh and account.expires_at and account.expires_at - REFRESH_MARGIN > utcnow():
                    return read_access_token(account)
                try:
                    tokens = refresh_tokens(read_refresh_token(account), self._http)
                except AuthExpired as exc:
                    account.status = IntegrationStatus.ERROR
                    account.last_error = str(exc)
                    db.commit()
                    raise
                store_tokens(account, tokens)
                db.commit()
                return tokens.access_token


def _safe_json(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
