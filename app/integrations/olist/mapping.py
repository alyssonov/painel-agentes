"""Tradução dos dados do Olist para o modelo do painel (funções puras, fáceis de testar)."""

from app.models.enums import Channel, KanbanStatus

# Situações do pedido na API v3 (parâmetro "situacao" de GET /pedidos).
ORDER_STATUS_LABELS = {
    8: "Dados incompletos",
    0: "Aberto",
    3: "Aprovado",
    4: "Preparando envio",
    1: "Faturado",
    7: "Pronto para envio",
    5: "Enviado",
    6: "Entregue",
    10: "Em devolução",
    2: "Cancelado",
    9: "Não entregue",
}

# Como cada situação aparece no quadro "Pedidos de hoje".
_KANBAN = {
    0: KanbanStatus.TO_PICK,
    3: KanbanStatus.TO_PICK,
    4: KanbanStatus.TO_PICK,
    1: KanbanStatus.PACKED,
    7: KanbanStatus.PACKED,
    5: KanbanStatus.SHIPPED,
    6: KanbanStatus.SHIPPED,
    8: KanbanStatus.PROBLEM,
    9: KanbanStatus.PROBLEM,
    10: KanbanStatus.PROBLEM,
    2: KanbanStatus.CANCELLED,
}

# Situações da nota fiscal na API v3 (parâmetro "situacao" de GET /notas).
INVOICE_STATUS_LABELS = {
    1: "Pendente",
    2: "Emitida",
    3: "Cancelada",
    4: "Enviada, aguardando recibo",
    5: "Rejeitada",
    6: "Autorizada",
    7: "Emitida (DANFE)",
    8: "Registrada",
    9: "Enviada, aguardando protocolo",
    10: "Denegada",
}


def to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def kanban_for(situacao) -> KanbanStatus:
    """Coluna do quadro para a situação do Olist. Desconhecida = 'Com problema' (para alguém olhar)."""
    return _KANBAN.get(to_int(situacao), KanbanStatus.PROBLEM)


def order_status_label(situacao) -> str:
    return ORDER_STATUS_LABELS.get(to_int(situacao), f"Situação {situacao}")


def problem_note_for(situacao) -> str | None:
    """Texto curto mostrado no cartão quando o pedido está 'Com problema'."""
    code = to_int(situacao)
    if code in (8, 9, 10):
        return ORDER_STATUS_LABELS[code]
    if code not in _KANBAN:
        return f"Situação desconhecida no Olist ({situacao})"
    return None


def channel_for(ecommerce: dict | None) -> Channel:
    """Descobre o marketplace pelo nome da integração/canal de venda informado pelo Olist."""
    text = " ".join(str(v) for v in (ecommerce or {}).values() if isinstance(v, str)).lower()
    if "mercado" in text or "meli" in text:
        return Channel.ML
    if "shopee" in text:
        return Channel.SHOPEE
    return Channel.OTHER


def short_customer_name(full_name: str | None) -> str:
    """Só o primeiro nome e a inicial do último (ex.: "Maria S."): guardamos o mínimo de dados pessoais."""
    parts = (full_name or "").split()
    if not parts:
        return "Cliente"
    first = parts[0].capitalize()
    return f"{first} {parts[-1][0].upper()}." if len(parts) > 1 else first


def product_cost(precos: dict | None) -> int | None:
    """Custo do produto em reais: custo médio se houver, senão o preço de custo."""
    precos = precos or {}
    for key in ("precoCustoMedio", "precoCusto"):
        value = precos.get(key)
        if value not in (None, "", 0, "0"):
            return value
    return None


def invoice_status_label(situacao) -> str:
    return INVOICE_STATUS_LABELS.get(to_int(situacao), f"Situação {situacao}")
