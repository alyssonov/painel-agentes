"""Valores fixos usados nas tabelas (guardados como texto no banco)."""

from enum import StrEnum


class Channel(StrEnum):
    """Marketplace de onde vem o pedido, anúncio ou pergunta."""

    ML = "ml"
    SHOPEE = "shopee"
    OTHER = "outro"  # pedidos do Olist de outros canais (loja própria, balcão...)


CHANNEL_LABELS = {Channel.ML: "Mercado Livre", Channel.SHOPEE: "Shopee", Channel.OTHER: "Outros"}


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
    CANCELLED = "cancelado"  # não aparece no quadro nem no faturamento


KANBAN_LABELS = {
    KanbanStatus.TO_PICK: "A separar",
    KanbanStatus.PACKED: "Embalado",
    KanbanStatus.SHIPPED: "Enviado",
    KanbanStatus.PROBLEM: "Com problema",
}

# Colunas que aparecem no quadro (pedidos cancelados ficam de fora).
KANBAN_COLUMNS = [KanbanStatus.TO_PICK, KanbanStatus.PACKED, KanbanStatus.SHIPPED, KanbanStatus.PROBLEM]


class QuestionStatus(StrEnum):
    NEW = "nova"
    PROPOSED = "proposta"  # resposta sugerida aguardando aprovação
    ANSWERED = "respondida"
    SIMULATED = "respondida_simulacao"  # aprovada no modo simulação: nada foi enviado
    HUMAN = "humano"  # encaminhada para uma pessoa responder
    CLOSED = "encerrada"  # apagada, banida ou fechada sem resposta no marketplace
    REVIEW = "em_analise"  # o marketplace ainda está analisando a pergunta (volta a "nova" quando liberar)


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


class SyncStatus(StrEnum):
    RUNNING = "rodando"
    OK = "ok"
    ERROR = "erro"


class ActionType(StrEnum):
    ANSWER_QUESTION = "responder_pergunta"
    SEND_MESSAGE = "enviar_mensagem"  # mensagem pós-venda para o comprador
    UPDATE_PRICE = "mudar_preco"  # novo preço de um anúncio


class ConversationStatus(StrEnum):
    """Situação de uma conversa pós-venda no painel."""

    NEW = "nova"  # última mensagem é do comprador e o agente ainda não viu
    PROPOSED = "proposta"  # resposta sugerida aguardando aprovação
    HUMAN = "humano"  # precisa de uma pessoa
    ANSWERED = "respondida"  # a última palavra é da loja
    SIMULATED = "respondida_simulacao"  # aprovada no modo simulação: nada foi enviado
    BLOCKED = "bloqueada"  # o marketplace não deixa responder (mediação, prazo, cancelada...)
    CLOSED = "encerrada"  # tirada da fila no painel


class ActionStatus(StrEnum):
    PENDING = "aguardando"  # esperando aprovação no painel
    REJECTED = "descartada"
    EXECUTED = "executada"
    SIMULATED = "simulada"  # aprovada no modo simulação: nada foi enviado de verdade
    FAILED = "falhou"
