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
