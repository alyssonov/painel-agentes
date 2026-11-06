"""Cliente da API do Mercado Livre — LEITURA.

Escritas (responder perguntas) NÃO ficam aqui: ficam em um módulo separado,
usado apenas pelo ActionGateway, que exige aprovação e respeita o DRY_RUN.

Documentação oficial consultada em 06/11/2026 (via busca; o portal recusa leitura automática):
- Perguntas: https://developers.mercadolivre.com.br/pt-br/gerenciamento-perguntas-respostas
- Vendas:    https://developers.mercadolivre.com.br/pt-br/gerenciamento-de-vendas
- Packs:     https://developers.mercadolivre.com.br/pt-br/gestao-packs

Endpoints usados (todos GET):
  /users/me                         conta do vendedor e reputação
  /questions/search?seller_id=...   perguntas (api_version=4)
  /items?ids=...                    títulos dos anúncios (até 20 por chamada)
  /items/{id}                       ficha do anúncio (preço, estoque, atributos)
  /items/{id}/description           descrição do anúncio
  /orders/search?seller=...         vendas, com a taxa (sale_fee) de cada item
"""

from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

from app.integrations.http import ReadOnlyApiClient

BASE_URL = "https://api.mercadolibre.com"
QUESTIONS_PAGE = 50
ORDERS_PAGE = 50
ITEMS_BATCH = 20
_UTC = ZoneInfo("UTC")


def to_ml_datetime(dt_utc: datetime) -> str:
    """UTC (sem fuso) → formato ISO com fuso que o Mercado Livre aceita nos filtros."""
    return dt_utc.replace(tzinfo=_UTC).strftime("%Y-%m-%dT%H:%M:%S.000-00:00")


class MLClient(ReadOnlyApiClient):
    label = "Mercado Livre"
    base_url = BASE_URL

    def get_me(self) -> dict:
        """GET /users/me — id, apelido e reputação do vendedor."""
        return self._get("/users/me") or {}

    def iter_questions(self, seller_id: int | str, *, status: str | None = None) -> Iterator[dict]:
        """GET /questions/search — perguntas do vendedor, das mais novas para as mais antigas."""
        offset = 0
        while True:
            data = self._get("/questions/search", {
                "seller_id": seller_id, "api_version": 4, "status": status,
                "sort_fields": "date_created", "sort_types": "DESC",
                "limit": QUESTIONS_PAGE, "offset": offset,
            }) or {}
            questions = data.get("questions") or []
            yield from questions
            offset += len(questions)
            total = data.get("total")
            if not questions or len(questions) < QUESTIONS_PAGE or (total is not None and offset >= int(total)):
                return

    def get_items(self, item_ids: list[str]) -> list[dict]:
        """GET /items?ids=... — dados básicos dos anúncios (em lotes de 20)."""
        items: list[dict] = []
        for start in range(0, len(item_ids), ITEMS_BATCH):
            batch = item_ids[start:start + ITEMS_BATCH]
            data = self._get("/items", {"ids": ",".join(batch), "attributes": "id,title,permalink,price"}) or []
            items += [entry.get("body") or {} for entry in data if entry.get("code") == 200]
        return items

    def get_item(self, item_id: str) -> dict | None:
        """GET /items/{id} — dados do anúncio usados para responder perguntas."""
        return self._get(f"/items/{item_id}")

    def get_item_description(self, item_id: str) -> str:
        """GET /items/{id}/description — texto da descrição (vazio se não houver)."""
        data = self._get(f"/items/{item_id}/description") or {}
        return data.get("plain_text") or ""

    def iter_orders(self, seller_id: int | str, *, created_from: datetime) -> Iterator[dict]:
        """GET /orders/search — vendas criadas desde a data (UTC)."""
        offset = 0
        while True:
            data = self._get("/orders/search", {
                "seller": seller_id, "order.date_created.from": to_ml_datetime(created_from),
                "sort": "date_desc", "limit": ORDERS_PAGE, "offset": offset,
            }) or {}
            results = data.get("results") or []
            yield from results
            offset += len(results)
            total = (data.get("paging") or {}).get("total")
            if not results or len(results) < ORDERS_PAGE or (total is not None and offset >= int(total)):
                return
