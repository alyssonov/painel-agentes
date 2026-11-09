"""Tradução dos dados do Mercado Livre para o modelo do painel."""

from app.integrations.olist.dates import to_cents
from app.models.enums import QuestionStatus

_POWER_SELLER = {"platinum": "MercadoLíder Platinum", "gold": "MercadoLíder Gold", "silver": "MercadoLíder"}
_LEVEL_COLOR = {
    "5_green": "Reputação verde",
    "4_light_green": "Reputação verde-clara",
    "3_yellow": "Reputação amarela",
    "2_orange": "Reputação laranja",
    "1_red": "Reputação vermelha",
}


def reputation_label(user: dict) -> str:
    """Texto da reputação para o card da Visão geral (ex.: "MercadoLíder Gold")."""
    rep = user.get("seller_reputation") or {}
    power = (rep.get("power_seller_status") or "").lower()
    if power in _POWER_SELLER:
        return _POWER_SELLER[power]
    return _LEVEL_COLOR.get(rep.get("level_id") or "", "Sem reputação ainda")


def question_status(ml_status: str | None) -> QuestionStatus:
    status = (ml_status or "").upper()
    if status == "UNANSWERED":
        return QuestionStatus.NEW
    if status == "ANSWERED":
        return QuestionStatus.ANSWERED
    if status == "UNDER_REVIEW":
        return QuestionStatus.REVIEW  # ainda em análise: vira "nova" quando o Mercado Livre liberar
    return QuestionStatus.CLOSED  # CLOSED_UNANSWERED, BANNED, DELETED...


def order_fee_cents(order: dict) -> int:
    """Taxa de venda do Mercado Livre em um pedido: soma de sale_fee × quantidade dos itens.

    A documentação descreve sale_fee como a comissão de venda de cada item.
    (Conferir no primeiro teste real com a tela "Vendas" do Mercado Livre.)
    """
    total = 0
    for item in order.get("order_items") or []:
        quantity = int(item.get("quantity") or 1)
        total += to_cents(item.get("sale_fee")) * quantity
    return total


def order_refs(order: dict) -> list[str]:
    """Números pelos quais o pedido pode aparecer no Olist (id do pedido e do pacote)."""
    refs = [str(order["id"])] if order.get("id") else []
    if order.get("pack_id"):
        refs.append(str(order["pack_id"]))
    return refs


def _sku_from(entity: dict) -> str | None:
    for attr in entity.get("attributes") or []:
        if attr.get("id") == "SELLER_SKU" and attr.get("value_name"):
            return str(attr["value_name"]).strip()
    value = entity.get("seller_custom_field")
    return str(value).strip() if value else None


def listing_rows(item: dict) -> list[dict]:
    """Uma linha por anúncio — ou uma por variação, cada uma com SKU e estoque próprios."""
    shipping = item.get("shipping") or {}
    base = {
        "item_id": item["id"],
        "title": item.get("title") or item["id"],
        "price_cents": to_cents(item.get("price")),
        "status": item.get("status") or "",
        "permalink": item.get("permalink"),
        "fulfillment": shipping.get("logistic_type") == "fulfillment",
    }
    variations = item.get("variations") or []
    if not variations:
        return [{**base, "external_id": item["id"], "sku": _sku_from(item), "variation_label": None,
                 "available_quantity": int(item.get("available_quantity") or 0)}]
    rows = []
    for var in variations:
        label = " / ".join(str(c.get("value_name")) for c in var.get("attribute_combinations") or []
                           if c.get("value_name"))
        rows.append({**base, "external_id": f"{item['id']}-{var.get('id')}", "sku": _sku_from(var) or _sku_from(item),
                     "variation_label": label or None,
                     "price_cents": to_cents(var.get("price")) or base["price_cents"],
                     "available_quantity": int(var.get("available_quantity") or 0)})
    return rows
