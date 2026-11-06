"""Utilidades dos testes do Olist: conta conectada e cliente sem esperas."""

from datetime import timedelta

import pytest

from app.config import get_settings
from app.core.db import get_sessionmaker, utcnow
from app.integrations.olist.auth import OlistTokenProvider
from app.integrations.olist.client import OlistClient
from app.integrations.tokens import TokenSet, store_tokens
from app.models import IntegrationAccount
from app.models.enums import IntegrationStatus, Provider


def connect_account(db, *, expires_in=14400):
    account = IntegrationAccount(provider=Provider.OLIST, account_name="Loja Teste", is_demo=False,
                                 status=IntegrationStatus.OK, extra={})
    store_tokens(account, TokenSet("access-atual", "refresh-atual", expires_in, 86400))
    db.add(account)
    db.commit()
    return account


def no_sleep_client_factory(sleeps=None):
    def factory():
        return OlistClient(OlistTokenProvider(get_sessionmaker()),
                           sleep=(sleeps.append if sleeps is not None else (lambda _s: None)))
    return factory


@pytest.fixture
def olist_env(monkeypatch):
    monkeypatch.setenv("OLIST_CLIENT_ID", "id-de-teste")
    monkeypatch.setenv("OLIST_CLIENT_SECRET", "segredo-de-teste")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


__all__ = ["connect_account", "no_sleep_client_factory", "olist_env", "utcnow", "timedelta"]
