"""Guarda e lê tokens OAuth no banco — sempre criptografados."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.db import utcnow
from app.core.security import decrypt_secret, encrypt_secret
from app.models.integration import IntegrationAccount


@dataclass(frozen=True)
class TokenSet:
    """Resposta do servidor de autorização."""

    access_token: str
    refresh_token: str | None
    expires_in: int  # segundos
    refresh_expires_in: int | None = None  # segundos

    @classmethod
    def from_response(cls, data: dict) -> "TokenSet":
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_in=int(data.get("expires_in") or 0),
            refresh_expires_in=int(data["refresh_expires_in"]) if data.get("refresh_expires_in") else None,
        )

    def __repr__(self) -> str:  # nunca mostrar os tokens em logs
        return f"TokenSet(expires_in={self.expires_in}, refresh_expires_in={self.refresh_expires_in})"


def store_tokens(account: IntegrationAccount, tokens: TokenSet, *, now: datetime | None = None) -> None:
    """Grava os tokens criptografados e as datas de validade na conta."""
    now = now or utcnow()
    account.access_token_enc = encrypt_secret(tokens.access_token)
    if tokens.refresh_token:
        account.refresh_token_enc = encrypt_secret(tokens.refresh_token)
    account.expires_at = now + timedelta(seconds=tokens.expires_in)
    account.refresh_expires_at = (
        now + timedelta(seconds=tokens.refresh_expires_in) if tokens.refresh_expires_in else None
    )


def clear_tokens(account: IntegrationAccount) -> None:
    account.access_token_enc = None
    account.refresh_token_enc = None
    account.expires_at = None
    account.refresh_expires_at = None


def read_access_token(account: IntegrationAccount) -> str | None:
    return decrypt_secret(account.access_token_enc) if account.access_token_enc else None


def read_refresh_token(account: IntegrationAccount) -> str | None:
    return decrypt_secret(account.refresh_token_enc) if account.refresh_token_enc else None
