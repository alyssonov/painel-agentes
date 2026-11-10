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
  /contas-receber    contas a receber (paginada) — previsão de recebimentos (swagger consultado em 11/11/2026)
"""

import logging
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from app.integrations.http import TokenProvider  # noqa: F401 - reexportado para quem importa daqui
from app.integrations.http import ReadOnlyApiClient
from app.integrations.olist.dates import to_olist_datetime

logger = logging.getLogger("integracoes.olist")

BASE_URL = "https://api.tiny.com.br/public-api/v3"
PAGE_SIZE = 100


class OlistClient(ReadOnlyApiClient):
    """Lê dados do Olist. Uso: `with OlistClient(provider) as client: ...`."""

    label = "Olist"
    base_url = BASE_URL

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

    def iter_orders(self, *, updated_since: datetime | None = None, created_since: datetime | None = None,
                    created_until: datetime | None = None) -> Iterator[dict]:
        """GET /pedidos — pedidos atualizados (ou criados) desde a data (UTC).

        created_until (dataFinal, consultado no swagger em 11/11/2026) limita a data
        de criação — usado para importar o histórico antigo aos poucos.
        """
        return self._paginate("/pedidos", {
            "dataAtualizacao": to_olist_datetime(updated_since),
            "dataInicial": to_olist_datetime(created_since, date_only=True),
            "dataFinal": to_olist_datetime(created_until, date_only=True),
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

    def iter_receivables(self, *, due_from: datetime, due_until: datetime,
                         situacao: str = "aberto") -> Iterator[dict]:
        """GET /contas-receber — contas a receber com vencimento no período (só leitura)."""
        return self._paginate("/contas-receber", {
            "situacao": situacao,
            "dataInicialVencimento": to_olist_datetime(due_from, date_only=True),
            "dataFinalVencimento": to_olist_datetime(due_until, date_only=True),
        })
