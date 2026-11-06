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

from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.integrations.errors import AuthExpired, IntegrationError, TemporaryError
from app.integrations.oauth import AccountTokenProvider, get_real_account
from app.integrations.tokens import TokenSet
from app.models.enums import Provider
from app.models.integration import IntegrationAccount

AUTH_URL = "https://accounts.tiny.com.br/realms/tiny/protocol/openid-connect/auth"
TOKEN_URL = "https://accounts.tiny.com.br/realms/tiny/protocol/openid-connect/token"

TIMEOUT = httpx.Timeout(30.0)


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
    return get_real_account(db, Provider.OLIST)


class OlistTokenProvider(AccountTokenProvider):
    """Entrega um token de acesso válido do Olist, renovando quando preciso."""

    def __init__(self, session_factory: sessionmaker[Session], http: httpx.Client | None = None) -> None:
        super().__init__(session_factory, Provider.OLIST, lambda rt: refresh_tokens(rt, http))


def _safe_json(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
