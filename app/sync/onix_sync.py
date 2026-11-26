"""Onix → painel: custo, marca, qtd. mínima e código de fábrica dos produtos. Só leitura do Onix.

Para cada produto do Olist (o código no Onix é o mesmo SKU do Olist, confirmado
pelo dono em 26/11/2026), consulta o Onix e grava no CADASTRO COMPLEMENTAR do
painel (product_extras). O Olist e o Onix não são alterados; o lucro real
continua usando o custo do Olist (o do Onix aparece ao lado, para comparar).

- custo do Onix: atualizado sempre (com histórico antes → depois quando muda);
- marca do Onix: guardada à parte (onix_brand) e usada quando o Olist não tem;
- estoque mínimo, código de fábrica e local: só preenchem o que estiver VAZIO
  (nunca sobrescrevem o que a pessoa escreveu no painel).
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.core import audit
from app.core.db import utcnow
from app.integrations.errors import IntegrationError, TemporaryError
from app.integrations.onix.client import OnixClient
from app.integrations.onix.mapping import parse
from app.models import Product, SyncRun
from app.models.enums import SyncStatus
from app.services.products import extra_for, set_cost, snapshot

logger = logging.getLogger("painel.onix")
PROVIDER = "onix"
MAX_CONSECUTIVE_FAILURES = 5  # Onix desligado: para logo, em vez de tentar produto por produto


def run_onix_sync(session_factory: sessionmaker[Session], *, client: OnixClient | None = None) -> SyncRun | None:
    settings = get_settings()
    if not settings.onix_configured and client is None:
        return None
    with session_factory() as db:
        run = SyncRun(provider=PROVIDER, status=SyncStatus.RUNNING, started_at=utcnow(), counts={})
        db.add(run)
        db.commit()
        counts = {"consultados": 0, "atualizados": 0, "custos_mudaram": 0, "nao_encontrados": 0}
        own = client is None
        client = client or OnixClient(settings.onix_api_url)
        failures = 0
        try:
            products = list(db.scalars(select(Product).where(Product.is_active.is_(True), Product.is_demo.is_(False))))
            # O Onix leva ~2 s por produto: nunca deixar o banco preso esperando a rede.
            # Cada produto é gravado e liberado na hora (antes travava o painel inteiro
            # por minutos, e quem tentasse gravar — ex.: conectar o Olist — dava erro).
            db.commit()
            for product in products:
                try:
                    data = client.get_product(product.sku)
                    failures = 0
                except TemporaryError:
                    failures += 1
                    if failures >= MAX_CONSECUTIVE_FAILURES:
                        raise
                    continue
                counts["consultados"] += 1
                if data is None:
                    counts["nao_encontrados"] += 1
                    continue
                info = parse(data, settings.onix_cost_field)
                extra = extra_for(db, product)
                before = snapshot(extra)
                old_cost = extra.onix_cost_cents
                if info.cost_cents is not None:
                    set_cost(extra, info.cost_cents)
                extra.onix_brand = info.brand
                if extra.min_stock is None and info.min_qty:
                    extra.min_stock = info.min_qty
                if not extra.supplier_code and info.factory_code:
                    extra.supplier_code = info.factory_code
                if not extra.location and info.location:
                    extra.location = info.location
                after = snapshot(extra)
                if after != before:
                    counts["atualizados"] += 1
                    if old_cost != extra.onix_cost_cents:
                        counts["custos_mudaram"] += 1
                    audit.record(db, actor="system", event="produto.cadastro_alterado", target_type="produto",
                                 target_id=product.id, details={**audit.changes(before, after), "origem": "Onix"})
                db.commit()
            run.status = SyncStatus.OK
        except IntegrationError as exc:
            run.status = SyncStatus.ERROR
            run.error = str(exc)[:500]
            logger.warning("Onix: %s", exc)
        finally:
            if own:
                client.__exit__(None, None, None)
        run.counts = counts
        run.finished_at = utcnow()
        audit.record(db, actor="system", event=f"onix.sincronizacao.{run.status}", details=counts)
        db.commit()
        return run
