"""Erros das integrações, com mensagens em português simples para o painel."""


class IntegrationError(Exception):
    """Erro ao falar com uma integração. A mensagem pode ser mostrada no painel."""


class TemporaryError(IntegrationError):
    """Falha passageira (internet, servidor fora do ar, limite de requisições).

    Vale tentar de novo depois de alguns segundos.
    """


class AuthExpired(IntegrationError):
    """A autorização expirou ou foi revogada: é preciso reconectar a conta."""


class NotConnected(IntegrationError):
    """A conta ainda não foi conectada."""
