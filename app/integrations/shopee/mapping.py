"""Shopee → formato do painel (só leitura; nenhuma regra de negócio da Shopee fora daqui).

Documentação oficial consultada em 26/11/2026 (texto copiado do navegador pelo usuário):
- v2.order.get_order_detail: https://open.shopee.com/documents/v2/v2.order.get_order_detail?module=94&type=1
- v2.payment.get_escrow_detail: https://open.shopee.com/documents/v2/v2.payment.get_escrow_detail?module=97&type=1
- v2.shop.get_shop_info: https://open.shopee.com/documents/v2/v2.shop.get_shop_info?module=92&type=1

Envios: a situação do pedido da Shopee é traduzida para as mesmas palavras usadas
nos envios do Mercado Livre (pending, ready_to_ship, shipped, delivered...), assim
o quadro "Envios de hoje" e a central de pedidos tratam os dois do mesmo jeito.
"""

from dataclasses import dataclass
from typing import Any

from app.integrations.shopee.client import from_timestamp

# order_status da Shopee → (status, substatus) no padrão dos envios do painel.
# PROCESSED = envio já preparado (etiqueta gerada) → "Embalado" no quadro.
STATUS_MAP: dict[str, tuple[str, str | None]] = {
    "UNPAID": ("pending", None),
    "READY_TO_SHIP": ("ready_to_ship", None),
    "RETRY_SHIP": ("ready_to_ship", None),
    "PROCESSED": ("ready_to_ship", "printed"),
    "SHIPPED": ("shipped", None),
    "TO_CONFIRM_RECEIVE": ("shipped", None),
    "COMPLETED": ("delivered", None),
    "TO_RETURN": ("delivered", "to_return"),
    "IN_CANCEL": ("cancelled", "in_cancel"),
    "CANCELLED": ("cancelled", None),
}
# Pedidos sem taxa a conferir (não pagos ou cancelados).
NO_ESCROW = {"UNPAID", "CANCELLED", "IN_CANCEL"}
# Campos opcionais pedidos no get_order_detail (só o necessário; sem endereço nem telefone).
DETAIL_FIELDS = "item_list,package_list,pay_time,total_amount,fulfillment_flag,pickup_done_time"


def to_cents(value: Any) -> int | None:
    """Valor da Shopee (float em reais) → centavos. None se não vier."""
    if value in (None, ""):
        return None
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


@dataclass
class ShopeeShipment:
    order_sn: str
    shipment_id: str
    status: str
    substatus: str | None
    logistic_type: str | None
    dispatch_deadline: Any
    shipped_at: Any


def shipment_from(order: dict) -> ShopeeShipment:
    """Envio (situação e prazo de despacho) a partir de um pedido do get_order_detail."""
    status, substatus = STATUS_MAP.get(str(order.get("order_status") or ""), ("pending", None))
    packages = order.get("package_list") or []
    package = str(packages[0].get("package_number") or "") if packages else ""
    logistic = "fulfillment" if order.get("fulfillment_flag") == "fulfilled_by_shopee" else None
    shipped = from_timestamp(order.get("pickup_done_time")) if status in ("shipped", "delivered") else None
    return ShopeeShipment(order_sn=str(order["order_sn"]), shipment_id=package or str(order["order_sn"]),
                          status=status, substatus=substatus, logistic_type=logistic,
                          dispatch_deadline=from_timestamp(order.get("ship_by_date")), shipped_at=shipped)


@dataclass
class ShopeeIncome:
    fee_cents: int
    net_cents: int | None
    seller_shipping_cents: int


def _first(data: dict, *keys: str) -> Any:
    for key in keys:
        if data.get(key) is not None:
            return data[key]
    return None


def income_from(escrow: dict) -> ShopeeIncome:
    """Taxa da Shopee e valor que a loja recebe, a partir do get_escrow_detail.

    Taxa = comissão + taxa de serviço + taxa de transação + taxa por pedido + comissão de
    afiliados. No Brasil, usa os valores "net_" (já com o desconto que a Shopee devolve).
    Valor recebido = escrow_amount (o "Valor a receber" do pedido; muda até concluir).
    """
    income = escrow.get("order_income") or {}
    parts = (
        _first(income, "net_commission_fee", "commission_fee"),
        _first(income, "net_service_fee", "service_fee"),
        income.get("seller_transaction_fee"),
        income.get("seller_order_processing_fee"),
        income.get("order_ams_commission_fee"),
    )
    fee = sum(to_cents(p) or 0 for p in parts)
    # final_shipping_fee: negativo = parte do frete que fica com a loja.
    final_shipping = to_cents(income.get("final_shipping_fee")) or 0
    return ShopeeIncome(fee_cents=max(fee, 0), net_cents=to_cents(income.get("escrow_amount")),
                        seller_shipping_cents=max(-final_shipping, 0))
