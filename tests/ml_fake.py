"""API do Mercado Livre simulada para os testes (respx)."""

import re

import httpx
import respx

from app.integrations.mercadolivre.auth import TOKEN_URL
from app.integrations.mercadolivre.client import BASE_URL

ME = {"id": 123456, "nickname": "LOJATESTE",
      "seller_reputation": {"level_id": "5_green", "power_seller_status": "gold", "metrics": {
          "sales": {"period": "60 days", "completed": 420},
          "claims": {"period": "60 days", "rate": 0.0175, "value": 7},
          "delayed_handling_time": {"period": "60 days", "rate": 0.03, "value": 12},
          "cancellations": {"period": "60 days", "rate": 0.002, "value": 1}}}}

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

# Anúncios completos (lista de anúncios da loja): um simples e um com variações.
FULL_ITEMS = {
    "MLB1": {"id": "MLB1", "title": "Caneca Térmica 500ml", "price": 69.9, "available_quantity": 7,
             "status": "active", "permalink": "https://produto.mercadolivre.com.br/MLB1",
             "attributes": [{"id": "SELLER_SKU", "value_name": "CAN-01"}], "shipping": {"logistic_type": "xd_drop_off"}},
    "MLB2": {"id": "MLB2", "title": "Garrafa Térmica 1L", "price": 99.9, "available_quantity": 50,
             "status": "active", "attributes": [], "shipping": {"logistic_type": "fulfillment"},
             "variations": [
                 {"id": 11, "available_quantity": 30, "attribute_combinations": [{"value_name": "Preta"}],
                  "attributes": [{"id": "SELLER_SKU", "value_name": "GAR-01"}]},
                 {"id": 12, "available_quantity": 20, "attribute_combinations": [{"value_name": "Azul"}],
                  "attributes": [{"id": "SELLER_SKU", "value_name": "GAR-02"}]}]},
}

ORDERS = [
    # pedido simples: taxa 2 × 10,49
    {"id": 2000001, "pack_id": None, "order_items": [{"quantity": 2, "sale_fee": 10.49}],
     "status": "paid", "shipping": {"id": 4100001}},
    # pacote com dois pedidos: no Olist vira um pedido só (número do pacote)
    {"id": 2000002, "pack_id": 3000001, "order_items": [{"quantity": 1, "sale_fee": 5.0}],
     "status": "paid", "shipping": {"id": 4100002}},
    {"id": 2000003, "pack_id": 3000001, "order_items": [{"quantity": 1, "sale_fee": 7.5}]},
]


def _questions(request):
    status = request.url.params.get("status")
    data = [q for q in QUESTIONS if not status or q["status"] == status]
    return httpx.Response(200, json={"total": len(data), "limit": 50, "questions": data})


def _items(request):
    ids = request.url.params["ids"].split(",")
    return httpx.Response(200, json=[{"code": 200, "body": FULL_ITEMS.get(i, {"id": i, "title": ITEMS.get(i, "?")})}
                                     for i in ids])


# Envios: um pronto para enviar (prazo vem do SLA), um do Full, um já enviado.
SHIPMENTS = {
    "4100001": {"id": 4100001, "status": "ready_to_ship", "substatus": "ready_to_print", "logistic_type": "xd_drop_off"},
    "4100002": {"id": 4100002, "status": "ready_to_ship", "logistic_type": "fulfillment"},
}
SLA = {"4100001": {"status": "on_time", "expected_date": "2026-11-10T15:00:00.000-03:00"}}


def install(router: respx.Router) -> dict[str, respx.Route]:
    return {
        "token": router.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={
            "access_token": "APP_USR-novo", "token_type": "Bearer", "expires_in": 21600,
            "scope": "offline_access read write", "user_id": 123456, "refresh_token": "TG-novo"})),
        "me": router.get(f"{BASE_URL}/users/me").mock(return_value=httpx.Response(200, json=ME)),
        "questions": router.get(f"{BASE_URL}/questions/search").mock(side_effect=_questions),
        "items": router.get(f"{BASE_URL}/items").mock(side_effect=_items),
        "my_items": router.get(url__regex=re.escape(BASE_URL) + r"/users/\d+/items/search").mock(
            return_value=httpx.Response(200, json={"results": list(FULL_ITEMS), "paging": {"total": len(FULL_ITEMS)}})),
        "shipment": router.get(url__regex=re.escape(BASE_URL) + r"/shipments/(?P<sid>\d+)$").mock(
            side_effect=lambda r, sid: httpx.Response(200, json=SHIPMENTS.get(sid, {"status": "shipped"}))),
        "sla": router.get(url__regex=re.escape(BASE_URL) + r"/shipments/(?P<sid>\d+)/sla$").mock(
            side_effect=lambda r, sid: httpx.Response(200, json=SLA.get(sid, {}))),
        "orders": router.get(f"{BASE_URL}/orders/search").mock(return_value=httpx.Response(200, json={
            "results": ORDERS, "paging": {"total": len(ORDERS), "offset": 0, "limit": 50}})),
    }
