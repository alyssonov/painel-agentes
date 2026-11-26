"""Cliente da API de produtos do Onix (sistema da loja física, Foxconn) — SOMENTE LEITURA.

Documentação: fornecida pelo próprio fornecedor do Onix em 26/11/2026 (exemplo de
retorno com a explicação de cada campo, recebido pelo dono da loja).
Endpoint usado (GET): /api-produto/get-produto-por-id?id={código do produto}
→ JSON com o produto (cod_prod, descr_prod, marca_prod, custo_prod, vlmedio_prod,
custoatac_prod, qtdminima_prod, codfab_prod, local_prod...).

O endereço do servidor fica só no .env (ONIX_API_URL). Este cliente não tem
nenhum método de escrita.
"""

import time
from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.integrations.errors import IntegrationError, TemporaryError

PRODUCT_PATH = "/api-produto/get-produto-por-id"
TIMEOUT = httpx.Timeout(15.0)
PAUSE_SECONDS = 0.25  # entre consultas: não sobrecarregar o computador da loja


class OnixClient:
    def __init__(self, base_url: str, *, http: httpx.Client | None = None, pause: float = PAUSE_SECONDS,
                 retry_wait: float = 1.0) -> None:
        self._http = http or httpx.Client(base_url=base_url.rstrip("/"), timeout=TIMEOUT)
        self._owns_http = http is None
        self._pause = pause
        self._retry_wait = retry_wait  # segundos-base entre novas tentativas (0 nos testes)

    def __enter__(self) -> "OnixClient":
        return self

    def __exit__(self, *_exc) -> None:
        if self._owns_http:
            self._http.close()

    def get_product(self, code: str) -> dict[str, Any] | None:
        """GET do produto pelo código do Onix. None quando não existe."""
        for attempt in Retrying(retry=retry_if_exception_type(TemporaryError), stop=stop_after_attempt(3),
                                wait=wait_exponential(multiplier=self._retry_wait, min=0, max=8 * self._retry_wait),
                                reraise=True):
            with attempt:
                return self._get_once(code)
        return None  # pragma: no cover

    def _get_once(self, code: str) -> dict[str, Any] | None:
        if self._pause:
            time.sleep(self._pause)
        try:
            response = self._http.get(PRODUCT_PATH, params={"id": code}, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise TemporaryError("O computador do Onix não respondeu (desligado ou fora da rede?).") from exc
        if response.status_code == 404:
            return None
        if response.status_code >= 500 or response.status_code == 429:
            raise TemporaryError(f"O Onix está instável (erro {response.status_code}).")
        if response.status_code != 200:
            raise IntegrationError(f"O Onix recusou a consulta (erro {response.status_code}).")
        try:
            data = response.json()
        except ValueError:
            return None  # resposta vazia/HTML: tratado como "não encontrado"
        if isinstance(data, list):
            data = data[0] if data else None
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        if not isinstance(data, dict) or data.get("cod_prod") in (None, ""):
            return None
        return data
