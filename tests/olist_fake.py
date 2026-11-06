"""API do Olist simulada para os testes (respx intercepta as chamadas httpx).

Os formatos seguem a especificação OpenAPI oficial "Olist ERP API v3" 3.1.
"""

import re

import httpx
import respx

from app.integrations.olist.auth import TOKEN_URL
from app.integrations.olist.client import BASE_URL

PRODUCTS = [
    {"id": 101, "sku": "CAN-01", "descricao": "Caneca Térmica", "situacao": "A", "tipo": "S",
     "precos": {"preco": 69.9, "precoPromocional": 0, "precoCusto": 20.0, "precoCustoMedio": 21.5}},
    {"id": 102, "sku": "GAR-01", "descricao": "Garrafa 1L", "situacao": "A", "tipo": "S",
     "precos": {"preco": 99.9, "precoCusto": 34.5, "precoCustoMedio": 0}},
    {"id": 103, "sku": "OLD-01", "descricao": "Produto excluído", "situacao": "E", "tipo": "S",
     "precos": {"preco": 10}},
]

STOCK = [
    {"idProduto": 101, "sku": "CAN-01", "nome": "Caneca Térmica", "saldo": 4, "reservado": 1, "disponivel": 3},
    {"idProduto": 102, "sku": "GAR-01", "nome": "Garrafa 1L", "saldo": 40.0, "reservado": 0, "disponivel": 40},
    {"idProduto": 999, "sku": "X", "nome": "Desconhecido", "saldo": 1, "reservado": 0, "disponivel": 1},
]


def order_summary(order_id, situacao, canal, data="2026-11-06 10:15:00"):
    return {"id": order_id, "situacao": situacao, "numeroPedido": 5000 + order_id,
            "ecommerce": {"id": 1, "nome": canal, "numeroPedidoEcommerce": f"EC{order_id}",
                          "canalVenda": canal},
            "dataCriacao": data, "cliente": {"nome": "Maria da Silva Souza"}, "valor": "139.80"}


ORDERS = [
    order_summary(1, 3, "Mercado Livre"),
    order_summary(2, 5, "Shopee"),
    order_summary(3, 2, "Mercado Livre"),  # cancelado
    order_summary(4, 8, "Loja própria"),   # dados incompletos -> problema
]


def order_detail(summary):
    return {**summary, "valorTotalProdutos": 139.8, "valorTotalPedido": 160.0,
            "valorFretePagoEmpresa": 22.9, "data": "2026-11-06",
            "itens": [
                {"produto": {"id": 101, "sku": "CAN-01", "descricao": "Caneca Térmica", "tipo": "P"},
                 "quantidade": 2, "valorUnitario": 69.9},
                {"produto": {"id": 500, "sku": "SERV", "descricao": "Instalação", "tipo": "S"},
                 "quantidade": 1, "valorUnitario": 10},
            ]}


INVOICES = [
    {"id": 7001, "situacao": "6", "tipo": "S", "numero": "123", "serie": "1", "chaveAcesso": "352611...",
     "dataEmissao": "2026-11-06", "valor": 139.8, "origem": {"id": "1", "tipo": "venda"}},
    {"id": 7002, "situacao": "5", "tipo": "S", "numero": "124", "serie": "1",
     "dataEmissao": "2026-11-06", "valor": 50, "origem": {"id": "2", "tipo": "venda"}},
]

INFO = {"razaoSocial": "Loja Teste LTDA", "fantasia": "Loja Teste", "cpfCnpj": "12.345.678/0001-90",
        "regimeTributario": 3}


def _page(items, request):
    limit = int(request.url.params.get("limit", 100))
    offset = int(request.url.params.get("offset", 0))
    return httpx.Response(200, json={"itens": items[offset:offset + limit],
                                     "paginacao": {"limit": limit, "offset": offset, "total": len(items)}},
                          headers={"X-RateLimit-Limit": "120", "X-RateLimit-Remaining": "100",
                                   "X-RateLimit-Reset": "30"})


def install(router: respx.Router, *, orders=None, products=None) -> dict[str, respx.Route]:
    """Registra as rotas falsas do Olist no roteador do respx."""
    orders = ORDERS if orders is None else orders
    products = PRODUCTS if products is None else products
    by_id = {str(o["id"]): o for o in orders}
    routes = {
        "token": router.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={
            "access_token": "novo-access", "refresh_token": "novo-refresh",
            "expires_in": 14400, "refresh_expires_in": 86400})),
        "info": router.get(f"{BASE_URL}/info").mock(return_value=httpx.Response(200, json=INFO)),
        "produtos": router.get(f"{BASE_URL}/produtos").mock(side_effect=lambda r: _page(products, r)),
        "estoque": router.get(f"{BASE_URL}/estoque").mock(side_effect=lambda r: _page(STOCK, r)),
        "pedidos": router.get(f"{BASE_URL}/pedidos").mock(side_effect=lambda r: _page(orders, r)),
        "pedido": router.get(url__regex=re.escape(BASE_URL) + r"/pedidos/(?P<oid>\d+)$").mock(
            side_effect=lambda r, oid: httpx.Response(200, json=order_detail(by_id[oid]))
            if oid in by_id else httpx.Response(404, json={})),
        "notas": router.get(f"{BASE_URL}/notas").mock(side_effect=lambda r: _page(INVOICES, r)),
    }
    return routes
