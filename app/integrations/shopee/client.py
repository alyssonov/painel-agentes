"""Cliente da Shopee Open Platform (API v2 oficial) — SOMENTE LEITURA.

Como os outros clientes, herda de ReadOnlyApiClient: só faz GET. Escritas (se
um dia existirem) ficam em módulo separado, usado só pelo ActionGateway.

Documentação oficial consultada em 26/11/2026 (texto copiado do navegador pelo
usuário; o site não abre para leitura automática):
- Autenticação das APIs da loja: https://open.shopee.com/developer-guide/20
  partner_id, timestamp, access_token, shop_id e sign vão na query; sign =
  HMAC-SHA256(partner_key, partner_id + caminho + timestamp + access_token + shop_id).
- GET /api/v2/order/get_order_list
  https://open.shopee.com/documents/v2/v2.order.get_order_list (Order > get_order_list)
  time_range_field (create_time | update_time), time_from/time_to (no máximo 15
  dias), page_size 1–100, cursor/next_cursor, more; resposta em "response";
  erro "order.order_list_invalid_time" se o intervalo passar de 15 dias.

Consultadas em 26/11/2026 (mesmo jeito):
- GET /api/v2/order/get_order_detail
  https://open.shopee.com/documents/v2/v2.order.get_order_detail?module=94&type=1
  order_sn_list com até 50 números separados por vírgula; response_optional_fields
  escolhe os campos extras (pedimos só itens, pacote, datas e total — sem endereço).
- GET /api/v2/payment/get_escrow_detail
  https://open.shopee.com/documents/v2/v2.payment.get_escrow_detail?module=97&type=1
  um order_sn por vez; "order_income" traz escrow_amount e as taxas.
- GET /api/v2/shop/get_shop_info
  https://open.shopee.com/documents/v2/v2.shop.get_shop_info?module=92&type=1
  shop_name, status (NORMAL | BANNED | FROZEN) e expire_time da autorização —
  vêm na raiz da resposta, não dentro de "response".
"""

import time
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config import get_settings
from app.integrations.errors import AuthExpired, IntegrationError, TemporaryError
from app.integrations.http import RateLimited, ReadOnlyApiClient
from app.integrations.shopee.auth import api_host, sign

ORDER_LIST_PATH = "/api/v2/order/get_order_list"
ORDER_DETAIL_PATH = "/api/v2/order/get_order_detail"
ESCROW_PATH = "/api/v2/payment/get_escrow_detail"
SHOP_INFO_PATH = "/api/v2/shop/get_shop_info"
DETAIL_BATCH = 50
ORDER_PAGE = 100
MAX_WINDOW = timedelta(days=15)
_UTC = ZoneInfo("UTC")

# Erros que valem tentar de novo depois de alguns segundos.
_TEMPORARY = {"error_server", "error_network", "error_busy", "error_inner", "error_data"}
# Erros de acesso: renova o token uma vez antes de desistir.
_AUTH = {"error_auth", "common.error_auth", "invalid_access_token", "error_token", "invalid_acceess_token"}


def to_timestamp(dt_utc: datetime) -> int:
    """UTC (sem fuso) → segundos desde 1970, como a Shopee espera."""
    return int(dt_utc.replace(tzinfo=_UTC).timestamp())


def from_timestamp(value: Any) -> datetime | None:
    """Segundos desde 1970 → UTC sem fuso (padrão do banco)."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(number, _UTC).replace(tzinfo=None) if number > 0 else None


class ShopeeClient(ReadOnlyApiClient):
    label = "Shopee"

    def __init__(self, token_provider, shop_id: int | str, *, http: httpx.Client | None = None, **kwargs) -> None:
        self.base_url = api_host()
        super().__init__(token_provider, http=http, **kwargs)
        self._shop_id = int(shop_id)

    # A Shopee não usa o cabeçalho Authorization: tudo vai assinado na query.
    def _get_once(self, path: str, params: dict[str, Any]) -> Any:
        token = self._tokens.get_token()
        self._limiter.wait()
        data, status = self._call(path, params, token)
        error = str(data.get("error") or "")
        if status == 401 or error in _AUTH:
            token = self._tokens.get_token(force_refresh=True)
            data, status = self._call(path, params, token)
            error = str(data.get("error") or "")
            if status == 401 or error in _AUTH:
                raise AuthExpired("A Shopee recusou o acesso. Clique em “Conectar de novo”.")
        if status == 429 or error == "error_rate_limit" or "rate_limit" in error:
            raise RateLimited(self.label, 30.0)
        if status >= 500 or error in _TEMPORARY:
            raise TemporaryError(f"A Shopee está instável ({error or status}). Tentando de novo.")
        if error == "error_permission" or status == 403:
            raise IntegrationError(f"O app da Shopee não tem permissão para ler {path}. Confira o tipo do app "
                                   "no Console e conecte de novo.")
        if error:
            raise IntegrationError(f"A Shopee recusou a consulta {path} ({error}): "
                                   f"{str(data.get('message') or '')[:200]}")
        if status != 200:
            raise IntegrationError(f"A Shopee recusou a consulta {path} (erro {status}).")
        # Quase todas as respostas vêm dentro de "response"; a get_shop_info vem na raiz.
        return data["response"] if isinstance(data.get("response"), dict) else data

    def _call(self, path: str, params: dict[str, Any], token: str) -> tuple[dict, int]:
        timestamp = int(time.time())
        query = {
            **params,
            "partner_id": int(get_settings().shopee_partner_id.strip()),
            "timestamp": timestamp,
            "access_token": token,
            "shop_id": self._shop_id,
            "sign": sign(path, timestamp, token, self._shop_id),
        }
        try:
            response = self._http.get(path, params=query, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise TemporaryError("Sem conexão com a Shopee no momento.") from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        return (data if isinstance(data, dict) else {}), response.status_code

    # --- Pedidos ---------------------------------------------------------------

    def iter_order_sns(self, since_utc: datetime, until_utc: datetime, *,
                       field: str = "update_time") -> Iterator[str]:
        """Números (order_sn) dos pedidos criados/atualizados no período.

        A Shopee aceita no máximo 15 dias por consulta: períodos maiores são
        lidos em fatias. Cada fatia é paginada pelo cursor.
        """
        start = since_utc
        while start < until_utc:
            end = min(start + MAX_WINDOW - timedelta(seconds=1), until_utc)
            cursor = ""
            while True:
                page = self._get(ORDER_LIST_PATH, {
                    "time_range_field": field,
                    "time_from": to_timestamp(start),
                    "time_to": to_timestamp(end),
                    "page_size": ORDER_PAGE,
                    "cursor": cursor,
                    "response_optional_fields": "order_status",
                })
                for order in page.get("order_list") or []:
                    if order.get("order_sn"):
                        yield str(order["order_sn"])
                cursor = str(page.get("next_cursor") or "")
                if not page.get("more") or not cursor:
                    break
            start = end + timedelta(seconds=1)

    def get_order_details(self, order_sns: list[str], fields: str) -> list[dict]:
        """Detalhes dos pedidos (até 50 por consulta; listas maiores são divididas)."""
        orders: list[dict] = []
        for i in range(0, len(order_sns), DETAIL_BATCH):
            page = self._get(ORDER_DETAIL_PATH, {"order_sn_list": ",".join(order_sns[i:i + DETAIL_BATCH]),
                                                 "response_optional_fields": fields})
            orders.extend(o for o in page.get("order_list") or [] if isinstance(o, dict) and o.get("order_sn"))
        return orders

    def get_escrow_detail(self, order_sn: str) -> dict:
        """Taxas e valor a receber de um pedido."""
        return self._get(ESCROW_PATH, {"order_sn": order_sn})

    def get_shop_info(self) -> dict:
        """Nome, situação da loja e validade da autorização."""
        return self._get(SHOP_INFO_PATH, {})
