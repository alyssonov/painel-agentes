"""Cliente da API v3 do Olist ERP — SOMENTE LEITURA.

REGRA DO PROJETO: este cliente só faz requisições GET. Não existe (e não pode
existir) método que altere estoque, pedidos, produtos ou notas fiscais no
Olist. Um teste automático (tests/test_olist_client.py) garante isso.

Documentação oficial consultada em 06/11/2026:
- Especificação OpenAPI "Olist ERP API v3" versão 3.1:
  https://api.tiny.com.br/public-api/v3/swagger/swagger.json
- Limites de requisição:
  https://api-docs.erp.olist.com/documentacao/comecando/limites-de-consulta

Endpoints usados (todos GET):
  /info              dados da empresa
  /produtos          lista de produtos (paginada)
  /estoque           saldo de estoque dos produtos (paginada)
  /pedidos           lista de pedidos (paginada)
  /pedidos/{id}      detalhe do pedido (itens, frete)
  /notas             lista de notas fiscais (paginada)
"""

import logging
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any, Protocol

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.integrations.errors import AuthExpired, IntegrationError, TemporaryError
from app.integrations.olist.dates import to_olist_datetime
from app.integrations.ratelimit import HeaderRateLimiter

logger = logging.getLogger("integracoes.olist")

BASE_URL = "https://api.tiny.com.br/public-api/v3"
PAGE_SIZE = 100
MAX_ATTEMPTS = 5
TIMEOUT = httpx.Timeout(30.0)


class TokenProvider(Protocol):
    def get_token(self, *, force_refresh: bool = False) -> str: ...


class _RateLimited(TemporaryError):
    """Erro 429: estourou o limite por minuto. Guarda quanto esperar."""

    def __init__(self, delay: float) -> None:
        super().__init__("Limite de requisições do Olist atingido. Aguardando para continuar.")
        self.delay = delay


class OlistClient:
    """Lê dados do Olist. Uso: `with OlistClient(provider) as client: ...`."""

    def __init__(self, token_provider: TokenProvider, *, http: httpx.Client | None = None,
                 limiter: HeaderRateLimiter | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._tokens = token_provider
        self._http = http or httpx.Client(base_url=BASE_URL, timeout=TIMEOUT)
        self._owns_http = http is None
        self._sleep = sleep
        self._limiter = limiter or HeaderRateLimiter(sleep=sleep)

    def __enter__(self) -> "OlistClient":
        return self

    def __exit__(self, *_exc) -> None:
        if self._owns_http:
            self._http.close()

    # --- Base: uma única forma de requisição, e ela é GET -------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Faz um GET com renovação de token, limite por minuto e novas tentativas."""
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
        raise IntegrationError("Falha inesperada ao ler do Olist.")  # pragma: no cover

    @staticmethod
    def _wait_strategy(retry_state) -> float:
        # Para limite estourado (429), espera o tempo que a API pediu.
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        if isinstance(exc, _RateLimited):
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
            raise TemporaryError("Sem conexão com o Olist no momento.") from exc
        self._limiter.update(response.headers)

        status = response.status_code
        if status == 200:
            return response.json()
        if status == 404:
            return None
        if status == 429:
            raise _RateLimited(self._limiter.reset_delay(response.headers))
        if status >= 500:
            raise TemporaryError(f"O Olist está instável (erro {status}). Tentando de novo.")
        if status == 401:
            raise AuthExpired("O Olist recusou o acesso. Clique em “Conectar” para autorizar de novo.")
        if status == 403:
            raise IntegrationError(
                "O aplicativo não tem permissão para ler estes dados no Olist "
                f"({path}). Confira as permissões do aplicativo e conecte de novo.")
        raise IntegrationError(f"O Olist recusou a consulta {path} (erro {status}): {_error_text(response)}")

    def _paginate(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict]:
        """Percorre todas as páginas de uma listagem (limit/offset)."""
        offset = 0
        while True:
            data = self._get(path, {**(params or {}), "limit": PAGE_SIZE, "offset": offset}) or {}
            items = data.get("itens") or []
            yield from items
            total = (data.get("paginacao") or {}).get("total")
            offset += len(items)
            if not items or (total is not None and offset >= int(total)) or len(items) < PAGE_SIZE:
                return

    # --- Consultas ----------------------------------------------------------

    def get_company_info(self) -> dict:
        """GET /info — razão social, nome fantasia, CNPJ, regime tributário."""
        return self._get("/info") or {}

    def iter_products(self, changed_since: datetime | None = None) -> Iterator[dict]:
        """GET /produtos — todos, ou só os alterados desde a data (UTC)."""
        return self._paginate("/produtos", {"dataAlteracao": to_olist_datetime(changed_since)})

    def iter_stock(self, changed_since: datetime | None = None) -> Iterator[dict]:
        """GET /estoque — saldo, reservado e disponível de cada produto."""
        return self._paginate("/estoque", {"dataAlteracao": to_olist_datetime(changed_since)})

    def iter_orders(self, *, updated_since: datetime | None = None,
                    created_since: datetime | None = None) -> Iterator[dict]:
        """GET /pedidos — pedidos atualizados (ou criados) desde a data (UTC)."""
        return self._paginate("/pedidos", {
            "dataAtualizacao": to_olist_datetime(updated_since),
            "dataInicial": to_olist_datetime(created_since, date_only=True),
            "orderBy": "asc",
        })

    def get_order(self, order_id: int | str) -> dict | None:
        """GET /pedidos/{id} — itens, valores e frete do pedido."""
        return self._get(f"/pedidos/{order_id}")

    def iter_invoices(self, *, created_since: datetime) -> Iterator[dict]:
        """GET /notas — notas fiscais de saída criadas desde a data (UTC)."""
        return self._paginate("/notas", {
            "tipo": "S",
            "dataInicial": to_olist_datetime(created_since, date_only=True),
        })


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
