"""Shopee: mantém a autorização viva (renova o acesso antes de vencer).

Nesta etapa só a conexão existe: a leitura de pedidos, taxas, perguntas e
envios entra na próxima, depois de consultar a documentação oficial de cada API.
O access_token vale 4 horas e o refresh_token 30 dias (uso único): renovar com
frequência evita que a pessoa precise "Conectar de novo".
"""

import logging

from sqlalchemy.orm import Session, sessionmaker

from app.core.db import utcnow
from app.integrations.errors import IntegrationError, NotConnected
from app.integrations.shopee.auth import ShopeeTokenProvider, get_shopee_account
from app.models.enums import IntegrationStatus

logger = logging.getLogger("painel.shopee")


def keep_shopee_alive(session_factory: sessionmaker[Session]) -> bool:
    """Renova o acesso da Shopee se estiver perto de vencer. True = conexão ok."""
    with session_factory() as db:
        account = get_shopee_account(db)
        if account is None or not account.refresh_token_enc:
            return False
    try:
        ShopeeTokenProvider(session_factory).get_token()
    except NotConnected:
        return False
    except IntegrationError as exc:  # AuthExpired já marca a conta com erro
        logger.warning("Shopee: não foi possível renovar o acesso: %s", exc)
        return False
    with session_factory() as db:
        account = get_shopee_account(db)
        if account is not None:
            account.status = IntegrationStatus.OK
            account.last_error = None
            account.last_ok_at = utcnow()
            db.commit()
    return True
