"""API do Mercado Livre simulada para os testes (respx)."""

import httpx
import respx

from app.integrations.mercadolivre.auth import TOKEN_URL
from app.integrations.mercadolivre.client import BASE_URL

ME = {"id": 123456, "nickname": "LOJATESTE",
      "seller_reputation": {"level_id": "5_green", "power_seller_status": "gold"}}

QUESTIONS = [
    {"id": 9001, "item_id": "MLB1", "status": "UNANSWERED", "text": "Serve para bebida gelada?",
     "date_created": "2026-11-06T10:00:00.000-03:00", "answer": None},
    {"id": 9002, "item_id": "MLB2", "status": "ANSWERED", "text": "Tem garantia?",
     "date_created": "2026-11-06T09:00:00.000-03:00",
     "answer": {"text": "Sim, 90 dias.", "date_created": "2026-11-06T09:05:00.000-03:00"}},
    {"id": 9003, "item_id": "MLB1", "status": "BANNED", "text": "",
     "date_created": "2026-11-06T08:00:00.000-03:00"},
    {"id": 8000, "item_id": "MLB1", "status": "ANSWERED", "text": "Pergunta muito antiga",
     "date_created": "2020-01-01T08:00:00.000-03:00", "answer": {"text": "ok"}},
]

ITEMS = {"MLB1": "Caneca Térmica 500ml", "MLB2": "Garrafa Térmica 1L"}

ORDERS = [
    # pedido simples: taxa 2 × 10,49
    {"id": 2000001, "pack_id": None, "order_items": [{"quantity": 2, "sale_fee": 10.49}]},
    # pacote com dois pedidos: no Olist vira um pedido só (número do pacote)
    {"id": 2000002, "pack_id": 3000001, "order_items": [{"quantity": 1, "sale_fee": 5.0}]},
    {"id": 2000003, "pack_id": 3000001, "order_items": [{"quantity": 1, "sale_fee": 7.5}]},
]


def _questions(request):
    status = request.url.params.get("status")
    data = [q for q in QUESTIONS if not status or q["status"] == status]
    return httpx.Response(200, json={"total": len(data), "limit": 50, "questions": data})


def _items(request):
    ids = request.url.params["ids"].split(",")
    return httpx.Response(200, json=[{"code": 200, "body": {"id": i, "title": ITEMS.get(i, "?")}} for i in ids])


def install(router: respx.Router) -> dict[str, respx.Route]:
    return {
        "token": router.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={
            "access_token": "APP_USR-novo", "token_type": "Bearer", "expires_in": 21600,
            "scope": "offline_access read write", "user_id": 123456, "refresh_token": "TG-novo"})),
        "me": router.get(f"{BASE_URL}/users/me").mock(return_value=httpx.Response(200, json=ME)),
        "questions": router.get(f"{BASE_URL}/questions/search").mock(side_effect=_questions),
        "items": router.get(f"{BASE_URL}/items").mock(side_effect=_items),
        "orders": router.get(f"{BASE_URL}/orders/search").mock(return_value=httpx.Response(200, json={
            "results": ORDERS, "paging": {"total": len(ORDERS), "offset": 0, "limit": 50}})),
    }
