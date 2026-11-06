"""Autorização OAuth 2 do Mercado Livre (com PKCE).

Documentação oficial consultada em 06/11/2026 (via busca; o portal recusa leitura automática):
- https://developers.mercadolivre.com.br/pt-br/autenticacao-e-autorizacao
- https://developers.mercadolivre.com.br/en_us/authentication-and-authorization
- https://global-selling.mercadolibre.com/devsite/authentication-and-authorization-global-selling

Pontos importantes:
- o token de acesso vale 6 horas;
- o refresh token é de USO ÚNICO: cada renovação devolve um novo, que precisa
  ser guardado (só o último gerado vale);
- o redirect_uri precisa ser idêntico ao cadastrado no aplicativo;
- o aplicativo foi criado com PKCE obrigatório (code_challenge S256).
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

AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
TIMEOUT = httpx.Timeout(30.0)


def authorize_url(state: str, code_challenge: str) -> str:
    """Endereço da tela de login/autorização do Mercado Livre."""
    settings = get_settings()
    query = urlencode({
        "response_type": "code",
        "client_id": settings.ml_client_id,
        "redirect_uri": settings.ml_redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    return f"{AUTH_URL}?{query}"


def _token_request(data: dict, http: httpx.Client | None = None) -> tuple[TokenSet, dict]:
    settings = get_settings()
    payload = {"client_id": settings.ml_client_id, "client_secret": settings.ml_client_secret, **data}
    client = http or httpx.Client(timeout=TIMEOUT)
    try:
        response = client.post(TOKEN_URL, data=payload, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise TemporaryError("Não foi possível falar com o Mercado Livre agora. Tente de novo em instantes.") from exc
    finally:
        if http is None:
            client.close()
    if response.status_code >= 500:
        raise TemporaryError("O Mercado Livre está instável. Tente de novo em instantes.")
    body = _safe_json(response)
    if response.status_code != 200:
        error = str(body.get("error", ""))
        if error == "invalid_grant":
            raise AuthExpired("A autorização do Mercado Livre expirou. Clique em “Conectar de novo”.")
        if error in ("invalid_client", "unauthorized_client"):
            raise IntegrationError("O Mercado Livre recusou as chaves do aplicativo. Confira ML_CLIENT_ID e "
                                   "ML_CLIENT_SECRET no .env.")
        raise IntegrationError(f"O Mercado Livre recusou a autorização ({error or response.status_code}).")
    return TokenSet.from_response(body), body


def exchange_code(code: str, code_verifier: str, http: httpx.Client | None = None) -> tuple[TokenSet, dict]:
    """Troca o código do retorno pelos tokens. Devolve também a resposta (traz o user_id)."""
    return _token_request({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": get_settings().ml_redirect_uri,
        "code_verifier": code_verifier,
    }, http)


def refresh_tokens(refresh_token: str, http: httpx.Client | None = None) -> TokenSet:
    tokens, _ = _token_request({"grant_type": "refresh_token", "refresh_token": refresh_token}, http)
    return tokens


def get_ml_account(db: Session) -> IntegrationAccount | None:
    return get_real_account(db, Provider.ML)


class MLTokenProvider(AccountTokenProvider):
    def __init__(self, session_factory: sessionmaker[Session], http: httpx.Client | None = None) -> None:
        super().__init__(session_factory, Provider.ML, lambda rt: refresh_tokens(rt, http))


def _safe_json(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
