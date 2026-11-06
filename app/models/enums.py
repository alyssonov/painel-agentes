"""Valores fixos usados nas tabelas (guardados como texto no banco)."""

from enum import StrEnum


class Channel(StrEnum):
    """Marketplace de onde vem o pedido, anúncio ou pergunta."""

    ML = "ml"
    SHOPEE = "shopee"


CHANNEL_LABELS = {Channel.ML: "Mercado Livre", Channel.SHOPEE: "Shopee"}


class Provider(StrEnum):
    """Integrações externas que exigem conta conectada."""

    OLIST = "olist"
    ML = "ml"
    SHOPEE = "shopee"


PROVIDER_LABELS = {Provider.ML: "Mercado Livre", Provider.SHOPEE: "Shopee", Provider.OLIST: "Olist ERP"}


class IntegrationStatus(StrEnum):
    OK = "ok"
    ERROR = "erro"
    DISCONNECTED = "desconectado"


class KanbanStatus(StrEnum):
    """Colunas do quadro 'Pedidos de hoje'."""

    TO_PICK = "a_separar"
    PACKED = "embalado"
    SHIPPED = "enviado"
    PROBLEM = "problema"


KANBAN_LABELS = {
    KanbanStatus.TO_PICK: "A separar",
    KanbanStatus.PACKED: "Embalado",
    KanbanStatus.SHIPPED: "Enviado",
    KanbanStatus.PROBLEM: "Com problema",
}


class QuestionStatus(StrEnum):
    NEW = "nova"
    PROPOSED = "proposta"  # resposta sugerida aguardando aprovação
    ANSWERED = "respondida"
    HUMAN = "humano"  # encaminhada para uma pessoa responder


class AgentMode(StrEnum):
    MANUAL = "manual"
    AUTO = "automatico"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "atencao"
    CRITICAL = "critico"


class AlertStatus(StrEnum):
    OPEN = "aberto"
    RESOLVED = "resolvido"


class UserRole(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operador"
