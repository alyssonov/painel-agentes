"""Base dos clientes de API: só leitura (GET), com novas tentativas e limites.

Usada pelos clientes do Olist e do Mercado Livre. Este módulo NÃO tem nenhuma
forma de escrita: escritas (responder pergunta, mensagem...) ficam em módulos
separados, usados só pelo ActionGateway (app/core/actions.py).
"""

import time
from collections.abc import Callable
from typing import Any, Protocol

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.integrations.errors import AuthExpired, IntegrationError, TemporaryError
from app.integrations.ratelimit import HeaderRateLimiter

MAX_ATTEMPTS = 5
TIMEOUT = httpx.Timeout(30.0)


class TokenProvider(Protocol):
    def get_token(self, *, force_refresh: bool = False) -> str: ...


class RateLimited(TemporaryError):
    """Erro 429: estourou o limite. Guarda quanto esperar antes de tentar de novo."""

    def __init__(self, label: str, delay: float) -> None:
        super().__init__(f"Limite de requisições do {label} atingido. Aguardando para continuar.")
        self.delay = delay


class ReadOnlyApiClient:
    """Cliente base. `label` é o nome mostrado nas mensagens (ex.: "Olist")."""

    label = "serviço"
    base_url = ""

    def __init__(self, token_provider: TokenProvider, *, http: httpx.Client | None = None,
                 limiter: HeaderRateLimiter | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._tokens = token_provider
        self._http = http or httpx.Client(base_url=self.base_url, timeout=TIMEOUT)
        self._owns_http = http is None
        self._sleep = sleep
        self._limiter = limiter or HeaderRateLimiter(sleep=sleep)

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        if self._owns_http:
            self._http.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET com renovação de token, limite por minuto e novas tentativas."""
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        retrying = Retrying(
            retry=retry_if_exception_type(TemporaryError),
            stop=stop_after_attempt(MAX_ATTEMPTS),
            wait=self._wait_strategy,
            sleep=self._sleep,
            reraise=True,
        )
        for attempt in retrying:
            with attempt:
                return self._get_once(path, clean)
        raise IntegrationError(f"Falha inesperada ao ler do {self.label}.")  # pragma: no cover

    @staticmethod
    def _wait_strategy(retry_state) -> float:
        # Para limite estourado (429), espera o tempo que a API pediu.
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        if isinstance(exc, RateLimited):
            return exc.delay
        return wait_exponential(multiplier=2, min=2, max=60)(retry_state)

    def _get_once(self, path: str, params: dict[str, Any]) -> Any:
        token = self._tokens.get_token()
        self._limiter.wait()
        try:
            response = self._http.get(path, params=params, headers=_auth(token))
            if response.status_code == 401:
                # Token recusado: renova uma vez e tenta de novo.
                token = self._tokens.get_token(force_refresh=True)
                response = self._http.get(path, params=params, headers=_auth(token))
        except httpx.HTTPError as exc:
            raise TemporaryError(f"Sem conexão com o {self.label} no momento.") from exc
        self._limiter.update(response.headers)

        status = response.status_code
        if status == 200:
            return response.json()
        if status == 404:
            return None
        if status == 429:
            raise RateLimited(self.label, self._limiter.reset_delay(response.headers))
        if status >= 500:
            raise TemporaryError(f"O {self.label} está instável (erro {status}). Tentando de novo.")
        if status == 401:
            raise AuthExpired(f"O {self.label} recusou o acesso. Clique em “Conectar de novo”.")
        if status == 403:
            raise IntegrationError(
                f"O aplicativo não tem permissão para ler estes dados no {self.label} "
                f"({path}). Confira as permissões do aplicativo e conecte de novo.")
        raise IntegrationError(f"O {self.label} recusou a consulta {path} (erro {status}): {_error_text(response)}")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(data, dict):
        return str(data.get("mensagem") or data.get("message") or data.get("detalhes") or data)[:200]
    return str(data)[:200]
