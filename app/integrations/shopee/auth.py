"""Autorização da Shopee Open Platform (API v2 oficial).

Documentação oficial consultada em 26/11/2026 (o site não abre para leitura
automática; o texto das páginas foi copiado do navegador pelo usuário):
- https://open.shopee.com/developer-guide/20  (Authorization and Authentication,
  "Last Updated: 2026-07-23")
- https://open.shopee.com/documents/v2/v2.public.get_access_token?module=104&type=1
- https://open.shopee.com/documents/v2/v2.public.refresh_access_token?module=104&type=1
  (descrita na mesma página do guia de autorização)

Pontos importantes:
- link de autorização (método novo): https://open.shopee.com.br/auth (Brasil) com
  partner_id, auth_type=seller, redirect_uri, response_type=code e state (CSRF);
  o domínio do redirect_uri precisa estar declarado no app ("Redirect URL Domain");
- a volta traz code (uso único, vale 10 minutos) e shop_id — ou main_account_id,
  quando quem autoriza é uma conta principal;
- assinatura: HMAC-SHA256 com a partner_key, em hexadecimal minúsculo, sobre
  partner_id + caminho + timestamp (APIs públicas) e, nas APIs da loja,
  + access_token + shop_id. O timestamp vale 5 minutos;
- GetAccessToken: POST /api/v2/auth/token/get; RefreshAccessToken: POST
  /api/v2/auth/access_token/get. partner_id, timestamp e sign vão na query;
  o resto vai no corpo JSON;
- access_token vale 4 horas; refresh_token vale 30 dias e é de USO ÚNICO (cada
  renovação devolve um novo, que precisa ser guardado); a autorização inteira
  vale no máximo 365 dias (o vendedor escolhe). Depois disso, "Conectar de novo";
- as respostas sempre trazem error (vazio quando dá certo), message e request_id.

Estas chamadas são só de autorização (pegar e renovar o acesso): não alteram
nada na loja.
"""

import hashlib
import hmac
import time
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.integrations.errors import AuthExpired, IntegrationError, TemporaryError
from app.integrations.oauth import AccountTokenProvider, get_real_account
from app.integrations.tokens import TokenSet
from app.models.enums import Provider
from app.models.integration import IntegrationAccount

# Endereços oficiais (tabela "Fixed authorization URL" e "Request Information").
AUTH_URL = {False: "https://open.shopee.com.br/auth", True: "https://open.sandbox.test-stable.shopee.com.br/auth"}
API_HOST = {False: "https://partner.shopeemobile.com", True: "https://openplatform.sandbox.test-stable.shopee.sg"}
TOKEN_PATH = "/api/v2/auth/token/get"
REFRESH_PATH = "/api/v2/auth/access_token/get"
REFRESH_TOKEN_DAYS = 30
TIMEOUT = httpx.Timeout(30.0)

# Erros que significam "a Shopee está instável, tente de novo" (não é culpa da conexão).
_TEMPORARY_ERRORS = {"error_server", "error_network", "error_busy", "error_inner"}


def api_host() -> str:
    return API_HOST[get_settings().shopee_sandbox]


def sign(path: str, timestamp: int, access_token: str | None = None, shop_id: int | None = None) -> str:
    """Assinatura oficial: HMAC-SHA256(partner_key, base) em hexadecimal minúsculo.

    base = partner_id + caminho + timestamp [+ access_token + shop_id, nas APIs da loja].
    """
    settings = get_settings()
    base = f"{settings.shopee_partner_id.strip()}{path}{timestamp}"
    if access_token is not None and shop_id is not None:
        base += f"{access_token}{shop_id}"
    return hmac.new(settings.shopee_partner_key.strip().encode(), base.encode(), hashlib.sha256).hexdigest()


def authorize_url(state: str) -> str:
    """Link da tela onde o vendedor entra na Shopee e autoriza o painel."""
    settings = get_settings()
    query = urlencode({
        "partner_id": settings.shopee_partner_id.strip(),
        "auth_type": "seller",
        "redirect_uri": settings.shopee_redirect_uri,
        "response_type": "code",
        "state": state,
    })
    return f"{AUTH_URL[settings.shopee_sandbox]}?{query}"


def _expires_in(value) -> int:
    """Validade do access_token em segundos.

    O guia mostra segundos (ex.: 14400), mas o exemplo da página GetAccessToken
    mostra um horário (ex.: 1767001812). Aceita os dois.
    """
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 4 * 3600
    if number > 1_000_000_000:  # é um horário (segundos desde 1970), não uma duração
        number -= int(time.time())
    return number if number > 0 else 4 * 3600


def _post(path: str, body: dict, http: httpx.Client | None, *, refreshing: bool) -> dict:
    settings = get_settings()
    timestamp = int(time.time())
    partner_id = int(settings.shopee_partner_id.strip())
    query = {"partner_id": partner_id, "timestamp": timestamp, "sign": sign(path, timestamp)}
    client = http or httpx.Client(timeout=TIMEOUT)
    try:
        response = client.post(f"{api_host()}{path}", params=query, json={**body, "partner_id": partner_id},
                               headers={"Content-Type": "application/json"})
    except httpx.HTTPError as exc:
        raise TemporaryError("Não foi possível falar com a Shopee agora. Tente de novo em instantes.") from exc
    finally:
        if http is None:
            client.close()
    if response.status_code >= 500 or response.status_code == 429:
        raise TemporaryError("A Shopee está instável. Tente de novo em instantes.")
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    error = str(data.get("error") or "")
    if response.status_code == 200 and not error and data.get("access_token"):
        return data
    if error in _TEMPORARY_ERRORS:
        raise TemporaryError("A Shopee está instável. Tente de novo em instantes.")
    request_id = data.get("request_id") or "-"
    if refreshing:
        # Refresh token vencido (30 dias), já usado ou autorização cancelada/expirada.
        raise AuthExpired("A autorização da Shopee expirou ou foi cancelada. Clique em “Conectar de novo”.")
    if error in ("invalid_code", "error_auth"):
        raise IntegrationError("O código da Shopee expirou ou já foi usado (vale 10 minutos). "
                               "Clique em “Conectar” de novo.")
    if error == "error_sign" or "sign" in str(data.get("message", "")).lower():
        raise IntegrationError("A Shopee recusou as chaves do app. Confira SHOPEE_PARTNER_ID e "
                               "SHOPEE_PARTNER_KEY no .env (e se são de produção ou de teste).")
    raise IntegrationError(f"A Shopee recusou a autorização ({error or response.status_code}; "
                           f"código de atendimento {request_id}).")


def _token_set(data: dict) -> TokenSet:
    return TokenSet(access_token=data["access_token"], refresh_token=data.get("refresh_token"),
                    expires_in=_expires_in(data.get("expire_in")),
                    refresh_expires_in=REFRESH_TOKEN_DAYS * 24 * 3600)


def exchange_code(code: str, *, shop_id: int | None = None, main_account_id: int | None = None,
                  http: httpx.Client | None = None) -> tuple[TokenSet, int]:
    """Troca o código da volta pelos tokens. Devolve os tokens e o shop_id da loja."""
    if shop_id is None and main_account_id is None:
        raise IntegrationError("A Shopee não informou qual loja foi autorizada. Clique em “Conectar” de novo.")
    body = {"code": code, **({"shop_id": shop_id} if shop_id is not None else {"main_account_id": main_account_id})}
    data = _post(TOKEN_PATH, body, http, refreshing=False)
    if shop_id is None:
        shops = data.get("shop_id_list") or []
        if not shops:
            raise IntegrationError("A conta principal autorizou, mas nenhuma loja foi marcada. "
                                   "Clique em “Conectar” de novo e marque a loja.")
        shop_id = int(shops[0])
    return _token_set(data), int(shop_id)


def refresh_tokens(refresh_token: str, shop_id: int, http: httpx.Client | None = None) -> TokenSet:
    """Renova o acesso. O refresh_token novo substitui o antigo (uso único)."""
    return _token_set(_post(REFRESH_PATH, {"refresh_token": refresh_token, "shop_id": int(shop_id)}, http,
                            refreshing=True))


def get_shopee_account(db: Session) -> IntegrationAccount | None:
    return get_real_account(db, Provider.SHOPEE)


class ShopeeTokenProvider(AccountTokenProvider):
    """Entrega um access_token válido, renovando com o shop_id guardado na conta."""

    def __init__(self, session_factory: sessionmaker[Session], http: httpx.Client | None = None) -> None:
        super().__init__(session_factory, Provider.SHOPEE, lambda rt: rt)  # não usado: ver _refresh
        self._http = http

    def _refresh(self, account: IntegrationAccount, refresh_token: str) -> TokenSet:
        if not account.external_id:
            raise AuthExpired("Falta a identificação da loja Shopee. Clique em “Conectar de novo”.")
        return refresh_tokens(refresh_token, int(account.external_id), self._http)
