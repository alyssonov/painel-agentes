"""Peças de OAuth comuns a todas as integrações (Olist, Mercado Livre, Shopee...)."""

import base64
import hashlib
import secrets
import threading
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import utcnow
from app.integrations.errors import AuthExpired, NotConnected
from app.integrations.tokens import TokenSet, read_access_token, read_refresh_token, store_tokens
from app.models.enums import PROVIDER_LABELS, IntegrationStatus
from app.models.integration import IntegrationAccount

# Renova o token quando faltar menos que isto para expirar.
REFRESH_MARGIN = timedelta(minutes=10)

# Uma trava por integração: evita duas renovações ao mesmo tempo no mesmo processo
# (importante quando o refresh token é de uso único, como no Mercado Livre).
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(provider: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(provider, threading.Lock())


def get_real_account(db: Session, provider: str) -> IntegrationAccount | None:
    """Conta real de uma integração (ignora a conta fictícia da simulação)."""
    return db.scalar(select(IntegrationAccount).where(
        IntegrationAccount.provider == provider, IntegrationAccount.is_demo.is_(False)))


def new_pkce_pair() -> tuple[str, str]:
    """Gera o par PKCE (code_verifier, code_challenge S256)."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


class AccountTokenProvider:
    """Entrega um token de acesso válido da conta, renovando quando preciso.

    refresh_fn recebe o refresh token atual e devolve os tokens novos.
    """

    def __init__(self, session_factory: sessionmaker[Session], provider: str,
                 refresh_fn: Callable[[str], TokenSet]) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self._refresh_fn = refresh_fn

    def _not_connected(self) -> NotConnected:
        return NotConnected(f"{PROVIDER_LABELS.get(self._provider, self._provider)} ainda não foi conectado.")

    def get_token(self, *, force_refresh: bool = False) -> str:
        with self._session_factory() as db:
            account = get_real_account(db, self._provider)
            if account is None or not account.access_token_enc:
                raise self._not_connected()
            fresh = account.expires_at and account.expires_at - REFRESH_MARGIN > utcnow()
            if fresh and not force_refresh:
                return read_access_token(account)

        with _lock_for(self._provider):
            with self._session_factory() as db:
                # Relê do banco: outro processo (painel ou worker) pode ter renovado agora.
                account = get_real_account(db, self._provider)
                if account is None or not account.refresh_token_enc:
                    raise self._not_connected()
                if not force_refresh and account.expires_at and account.expires_at - REFRESH_MARGIN > utcnow():
                    return read_access_token(account)
                try:
                    tokens = self._refresh_fn(read_refresh_token(account))
                except AuthExpired as exc:
                    account.status = IntegrationStatus.ERROR
                    account.last_error = str(exc)
                    db.commit()
                    raise
                store_tokens(account, tokens)
                db.commit()
                return tokens.access_token
