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
  /questions/{id}                   situação atual de uma pergunta
  /items?ids=...                    títulos dos anúncios (até 20 por chamada)
  /items/{id}                       ficha do anúncio (preço, estoque, atributos)
  /items/{id}/description           descrição do anúncio
  /orders/search?seller=...         vendas, com a taxa (sale_fee) de cada item
  /users/{id}/items/search          ids de todos os anúncios da loja (modo scan acima de 1000)
  /shipments/{id}                   situação do envio (pendente, pronto para enviar, enviado...)
  /shipments/{id}/sla               prazo limite de despacho (expected_date)
  /messages/unread, /messages/packs/{pack}/sellers/{seller}   pós-venda (sempre mark_as_read=false)
  /post-purchase/v1/claims/...      reclamações (busca, detalhe, se afeta a reputação)
    https://developers.mercadolivre.com.br/pt-br/gerenciamento-de-envios
    https://developers.mercadolivre.com.br/pt-br/mercado-envios-agora (consultado em 10/11/2026)
    https://developers.mercadolivre.com.br/itens-e-buscas
"""

from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

from app.integrations.errors import AuthExpired, IntegrationError, TemporaryError
from app.integrations.http import ReadOnlyApiClient

BASE_URL = "https://api.mercadolibre.com"
QUESTIONS_PAGE = 50
ORDERS_PAGE = 50
ITEMS_BATCH = 20
ITEMS_PAGE = 100
MESSAGES_PAGE = 20
MAX_MESSAGES = 100  # conversas pós-venda são curtas; o agente só usa as últimas
CLAIMS_PAGE = 30
MAX_CLAIMS_HISTORY = 3000
REVIEWS_PAGE = 50
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

    def get_question(self, question_id: str) -> dict | None:
        """GET /questions/{id} — situação atual de uma pergunta (api_version=4)."""
        return self._get(f"/questions/{question_id}", {"api_version": 4})

    def get_items(self, item_ids: list[str]) -> list[dict]:
        """GET /items?ids=... — dados básicos dos anúncios (em lotes de 20)."""
        items: list[dict] = []
        for start in range(0, len(item_ids), ITEMS_BATCH):
            batch = item_ids[start:start + ITEMS_BATCH]
            try:
                data = self._get("/items", {"ids": ",".join(batch), "attributes": "id,title,permalink,price"}) or []
            except (AuthExpired, TemporaryError):
                raise
            except IntegrationError:  # consulta em lote recusada: um por vez
                items += [item for item in (self.get_item(item_id) for item_id in batch) if item]
                continue
            if isinstance(data, dict):  # um anúncio só pode vir sem a "embalagem" da consulta múltipla
                data = [{"code": 200, "body": data}]
            for entry in data if isinstance(data, list) else []:
                if isinstance(entry, dict) and entry.get("code") == 200 and isinstance(entry.get("body"), dict):
                    items.append(entry["body"])
        return items

    def get_item(self, item_id: str) -> dict | None:
        """GET /items/{id} — dados do anúncio usados para responder perguntas."""
        return self._get(f"/items/{item_id}")

    def get_item_description(self, item_id: str) -> str:
        """GET /items/{id}/description — texto da descrição (vazio se não houver)."""
        data = self._get(f"/items/{item_id}/description") or {}
        return data.get("plain_text") or ""

    def iter_my_item_ids(self, seller_id: int | str, *, status: str = "active") -> Iterator[str]:
        """GET /users/{id}/items/search — ids dos anúncios da loja.

        Até 1000 anúncios usa limit/offset; acima disso, o modo "scan" com scroll_id
        (como manda a documentação "Itens e buscas").
        """
        path = f"/users/{seller_id}/items/search"
        data = self._get(path, {"status": status, "limit": ITEMS_PAGE, "offset": 0}) or {}
        total = int((data.get("paging") or {}).get("total") or 0)
        if total <= 1000:
            offset = 0
            while True:
                results = data.get("results") or []
                yield from results
                offset += len(results)
                if not results or offset >= total:
                    return
                data = self._get(path, {"status": status, "limit": ITEMS_PAGE, "offset": offset}) or {}
        params = {"status": status, "search_type": "scan", "limit": ITEMS_PAGE}
        data = self._get(path, params) or {}
        while True:
            results = data.get("results") or []
            yield from results
            scroll_id = data.get("scroll_id")
            if not results or not scroll_id:
                return
            data = self._get(path, {**params, "scroll_id": scroll_id}) or {}

    def get_items_full(self, item_ids: list[str]) -> list[dict]:
        """GET /items?ids=... — anúncios com estoque, preço, SKU e variações (lotes de 20)."""
        items: list[dict] = []
        attrs = "id,title,price,available_quantity,status,permalink,seller_custom_field,attributes,variations,shipping"
        for start in range(0, len(item_ids), ITEMS_BATCH):
            batch = item_ids[start:start + ITEMS_BATCH]
            try:
                data = self._get("/items", {"ids": ",".join(batch), "attributes": attrs}) or []
            except (AuthExpired, TemporaryError):
                raise
            except IntegrationError:
                # Plano B: algumas contas/aplicativos recebem 403 na consulta em lote,
                # mas a consulta de um anúncio por vez (GET /items/{id}) funciona.
                items += [item for item in (self.get_item(item_id) for item_id in batch) if item]
                continue
            for entry in data if isinstance(data, list) else []:
                if isinstance(entry, dict) and entry.get("code") == 200 and isinstance(entry.get("body"), dict):
                    items.append(entry["body"])
        return items

    def get_shipment(self, shipment_id: str) -> dict | None:
        """GET /shipments/{id} — situação e tipo logístico do envio."""
        return self._get(f"/shipments/{shipment_id}")

    def get_shipment_sla(self, shipment_id: str) -> dict | None:
        """GET /shipments/{id}/sla — prazo limite para despachar (expected_date)."""
        return self._get(f"/shipments/{shipment_id}/sla")

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

    # --- Pós-venda (Fase 6) -------------------------------------------------------------
    # Documentação oficial consultada em 10/11/2026:
    #   https://developers.mercadolivre.com.br/pt_br/mensagens-post-venda
    #   https://developers.mercadolivre.com.br/pt_br/mensagens-pendentes
    #   https://developers.mercadolivre.com.br/pt_br/mensagens-bloqueadas
    #   https://developers.mercadolivre.com.br/pt_br/gerenciar-reclamacoes

    def get_unread_conversations(self) -> list[dict]:
        """GET /messages/unread?role=seller — pacotes com mensagens do comprador ainda não lidas."""
        data = self._get("/messages/unread", {"role": "seller", "tag": "post_sale"}) or {}
        return data.get("results") or []

    def get_pack_messages(self, pack_id: str, seller_id: int | str) -> dict:
        """GET /messages/packs/{pack}/sellers/{seller} — conversa do pacote.

        IMPORTANTE: sem mark_as_read=false o Mercado Livre marca as mensagens como
        lidas. O painel só lê: quem decide quando ler é a pessoa, no marketplace.
        """
        path = f"/messages/packs/{pack_id}/sellers/{seller_id}"
        params = {"tag": "post_sale", "mark_as_read": "false", "limit": MESSAGES_PAGE, "offset": 0}
        data = self._get(path, params) or {}
        messages = list(data.get("messages") or [])
        total = int((data.get("paging") or {}).get("total") or 0)
        while len(messages) < min(total, MAX_MESSAGES):
            page = self._get(path, {**params, "offset": len(messages)}) or {}
            more = page.get("messages") or []
            if not more:
                break
            messages += more
        return {**data, "messages": messages}

    def iter_open_claims(self, seller_id: int | str) -> Iterator[dict]:
        """GET /post-purchase/v1/claims/search — reclamações abertas contra a loja."""
        offset = 0
        while True:
            data = self._get("/post-purchase/v1/claims/search", {
                "players.user_id": seller_id, "players.role": "respondent", "status": "opened",
                "limit": CLAIMS_PAGE, "offset": offset,
            }) or {}
            results = data.get("data") or []
            yield from results
            offset += len(results)
            total = (data.get("paging") or {}).get("total")
            if not results or len(results) < CLAIMS_PAGE or (total is not None and offset >= int(total)):
                return

    def get_claim(self, claim_id: str) -> dict | None:
        """GET /post-purchase/v1/claims/{id} — situação atual da reclamação."""
        return self._get(f"/post-purchase/v1/claims/{claim_id}")

    def get_claim_detail(self, claim_id: str) -> dict:
        """GET /post-purchase/v1/claims/{id}/detail — título, problema, responsável e prazo."""
        return self._get(f"/post-purchase/v1/claims/{claim_id}/detail") or {}

    def get_claim_reputation(self, claim_id: str) -> dict:
        """GET /post-purchase/v1/claims/{id}/affects-reputation — se afeta a reputação."""
        return self._get(f"/post-purchase/v1/claims/{claim_id}/affects-reputation") or {}

    # --- Preços (Fase 7) ----------------------------------------------------------------
    # Documentação oficial consultada em 11/11/2026:
    #   https://developers.mercadolivre.com.br/pt_br/referencias-de-precos
    #   https://developers.mercadolivre.com.br/pt_br/automatizacoes-de-precos
    #   https://developers.mercadolivre.com.br/pt_br/api-de-precos

    def get_price_suggestion_items(self, user_id: int | str) -> list[str]:
        """GET /suggestions/user/{id}/items — anúncios que têm referência de preço."""
        data = self._get(f"/suggestions/user/{user_id}/items") or {}
        return [str(i) for i in data.get("items") or []]

    def get_price_suggestion(self, item_id: str) -> dict | None:
        """GET /suggestions/items/{id}/details — preço de referência, concorrência e custos."""
        return self._get(f"/suggestions/items/{item_id}/details")

    def iter_automated_price_items(self, user_id: int | str) -> Iterator[dict]:
        """GET /pricing-automation/users/{id}/items — anúncios com automatização de preços do ML."""
        offset = 0
        while True:
            data = self._get(f"/pricing-automation/users/{user_id}/items", {"offset": offset, "limit": 100}) or {}
            results = data.get("items") or data.get("results") or []
            yield from results
            offset += len(results)
            total = (data.get("paging") or {}).get("total") or data.get("total")
            if not results or len(results) < 100 or (total is not None and offset >= int(total)):
                return

    # --- Avaliações e devoluções (Fase 7) -----------------------------------------------
    # Documentação oficial consultada em 11/11/2026:
    #   https://developers.mercadolivre.com.br/pt_br/opinioes-sobre-um-produto
    #   https://developers.mercadolivre.com.br/pt_br/gerenciar-reclamacoes (busca com range, motivos)

    def get_item_reviews(self, item_id: str, limit: int = REVIEWS_PAGE) -> dict:
        """GET /reviews/item/{id} — média, estrelas e as avaliações do anúncio."""
        return self._get(f"/reviews/item/{item_id}", {"limit": limit, "offset": 0}) or {}

    def iter_claims_since(self, seller_id: int | str, created_after: datetime) -> Iterator[dict]:
        """GET /post-purchase/v1/claims/search — reclamações (abertas e encerradas) criadas desde a data."""
        stamp = created_after.replace(tzinfo=_UTC).strftime("%Y-%m-%dT%H:%M:%S.000+00:00")
        offset = 0
        while offset < MAX_CLAIMS_HISTORY:
            data = self._get("/post-purchase/v1/claims/search", {
                "players.user_id": seller_id, "players.role": "respondent",
                "range": f"date_created:after:{stamp}", "limit": CLAIMS_PAGE, "offset": offset,
            }) or {}
            results = data.get("data") or []
            yield from results
            offset += len(results)
            total = (data.get("paging") or {}).get("total")
            if not results or len(results) < CLAIMS_PAGE or (total is not None and offset >= int(total)):
                return

    def get_claim_reason(self, reason_id: str) -> dict:
        """GET /post-purchase/v1/claims/reasons/{id} — explicação do motivo da reclamação."""
        return self._get(f"/post-purchase/v1/claims/reasons/{reason_id}") or {}
